#!/usr/bin/env python3
# This file is part of Xpra.
# Copyright (C) 2010-2021 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys
import os

from xpra.gst_common import (
    has_plugins, get_all_plugin_names,
    import_gst, get_gst_version,
    )
from xpra.sound.common import (
    FLAC_OGG, OPUS_OGG, OPUS_MKA, SPEEX_OGG, VORBIS_OGG, VORBIS_MKA, \
    AAC_MPEG4, WAV_LZ4, \
    VORBIS, FLAC, MP3, MP3_MPEG4, OPUS, SPEEX, WAV, WAVPACK, MP3_ID3V2, \
    MPEG4, MKA, OGG,
    )
from xpra.os_util import WIN32, OSX, POSIX, bytestostr
from xpra.util import csv, engs, parse_simple_dict, reverse_dict, envint, envbool
from xpra.log import Logger

log = Logger("sound", "gstreamer")


# pylint: disable=import-outside-toplevel

#used on the server (reversed):
XPRA_PULSE_SOURCE_DEVICE_NAME = "XPRA_PULSE_SOURCE_DEVICE_NAME"
XPRA_PULSE_SINK_DEVICE_NAME = "XPRA_PULSE_SINK_DEVICE_NAME"

GST_QUEUE_NO_LEAK             = 0
GST_QUEUE_LEAK_UPSTREAM       = 1
GST_QUEUE_LEAK_DOWNSTREAM     = 2
GST_QUEUE_LEAK_DEFAULT = GST_QUEUE_LEAK_DOWNSTREAM
MS_TO_NS = 1000000

GST_FLOW_OK = 0     #Gst.FlowReturn.OK


QUEUE_LEAK = envint("XPRA_SOUND_QUEUE_LEAK", GST_QUEUE_LEAK_DEFAULT)
if QUEUE_LEAK not in (GST_QUEUE_NO_LEAK, GST_QUEUE_LEAK_UPSTREAM, GST_QUEUE_LEAK_DOWNSTREAM):
    log.error("invalid leak option %s", QUEUE_LEAK)
    QUEUE_LEAK = GST_QUEUE_LEAK_DEFAULT

def get_queue_time(default_value=450, prefix=""):
    queue_time = int(os.environ.get(f"XPRA_SOUND_QUEUE_{prefix}TIME", default_value))*MS_TO_NS
    queue_time = max(0, queue_time)
    return queue_time


ALLOW_SOUND_LOOP = envbool("XPRA_ALLOW_SOUND_LOOP", False)
USE_DEFAULT_DEVICE = envbool("XPRA_USE_DEFAULT_DEVICE", True)
IGNORED_INPUT_DEVICES = os.environ.get("XPRA_SOUND_IGNORED_INPUT_DEVICES", "bell.ogg,bell.wav").split(",")
IGNORED_OUTPUT_DEVICES = os.environ.get("XPRA_SOUND_IGNORED_OUTPUT_DEVICES", "bell-window-system").split(",")
def force_enabled(codec_name):
    return os.environ.get("XPRA_SOUND_CODEC_ENABLE_%s" % codec_name.upper().replace("+", "_"), "0")=="1"


NAME_TO_SRC_PLUGIN = {
    "auto"          : "autoaudiosrc",
    "alsa"          : "alsasrc",
    "oss"           : "osssrc",
    "oss4"          : "oss4src",
    "jack"          : "jackaudiosrc",
    "osx"           : "osxaudiosrc",
    "test"          : "audiotestsrc",
    "pulse"         : "pulsesrc",
    "direct"        : "directsoundsrc",
    "wasapi"        : "wasapisrc",
    }
SRC_TO_NAME_PLUGIN = reverse_dict(NAME_TO_SRC_PLUGIN)
SRC_HAS_DEVICE_NAME = ["alsasrc", "osssrc", "oss4src", "jackaudiosrc", "pulsesrc", "directsoundsrc", "osxaudiosrc"]
PLUGIN_TO_DESCRIPTION = {
    "pulsesrc"      : "Pulseaudio",
    "jacksrc"       : "JACK Audio Connection Kit",
    }

NAME_TO_INFO_PLUGIN = {
    "auto"          : "Automatic audio source selection",
    "alsa"          : "ALSA Linux Sound",
    "oss"           : "OSS sound cards",
    "oss4"          : "OSS version 4 sound cards",
    "jack"          : "JACK audio sound server",
    "osx"           : "Mac OS X sound cards",
    "test"          : "Test signal",
    "pulse"         : "PulseAudio",
    "direct"        : "Microsoft Windows Direct Sound",
    "wasapi"        : "Windows Audio Session API",
    }


#format: encoder, container-formatter, decoder, container-parser, stream-compressor
#we keep multiple options here for the same encoding
#and will populate the ones that are actually available into the "CODECS" dict
CODEC_OPTIONS = [
        (VORBIS_MKA , "vorbisenc",      "matroskamux",  "vorbisdec",                    "matroskademux"),
        (VORBIS_MKA , "vorbisenc",      "webmmux",      "vorbisdec",                    "matroskademux"),
        #those two used to fail silently (older versions of gstreamer?)
        (VORBIS_OGG , "vorbisenc",      "oggmux",       "vorbisparse ! vorbisdec",      "oggdemux"),
        (VORBIS     , "vorbisenc",      None,           "vorbisparse ! vorbisdec",      None),
        (FLAC       , "flacenc",        None,           "flacparse ! flacdec",          None),
        (FLAC_OGG   , "flacenc",        "oggmux",       "flacparse ! flacdec",          "oggdemux"),
        (MP3_ID3V2  , "lamemp3enc",     "id3v2mux",     "mpegaudioparse ! mpg123audiodec", "id3demux"),
        (MP3        , "lamemp3enc",     None,           "mpegaudioparse ! mpg123audiodec", None),
        (WAV        , "wavenc",         None,           "wavparse",                     None),
        (WAV_LZ4    , "wavenc",         None,           "wavparse",                     None,               "lz4"),
        (OPUS_OGG   , "opusenc",        "oggmux",       "opusdec",                      "oggdemux"),
        (OPUS       , "opusenc",        None,           "opusparse ! opusdec",          None),
        #this can cause "could not link opusenc0 to webmmux0"
        (OPUS_MKA   , "opusenc",        "matroskamux",  "opusdec",                      "matroskademux"),
        (OPUS_MKA   , "opusenc",        "webmmux",      "opusdec",                      "matroskademux"),
        (SPEEX_OGG  , "speexenc",       "oggmux",       "speexdec",                     "oggdemux"),
        (WAVPACK    , "wavpackenc",      None,          "wavpackparse ! wavpackdec",    None),
        (AAC_MPEG4  , "faac",           "mp4mux",       "faad",                         "qtdemux"),
        (AAC_MPEG4  , "avenc_aac",      "mp4mux",       "avdec_aac",                    "qtdemux"),
        (AAC_MPEG4  , "voaacenc",       "mp4mux",       "faad",                         "qtdemux"),
            ]

MUX_OPTIONS = [
               (OGG,    "oggmux",   "oggdemux"),
               (MKA,    "webmmux",  "matroskademux"),
               (MKA,    "matroskamux",  "matroskademux"),
               (MPEG4,  "mp4mux",   "qtdemux"),
              ]
emux = [x for x in os.environ.get("XPRA_MUXER_OPTIONS", "").split(",") if len(x.strip())>0]
if emux:
    mo = [v for v in MUX_OPTIONS if v[0] in emux]
    if mo:
        MUX_OPTIONS = mo
    else:
        log.warn("Warning: invalid muxer options %s", emux)
    del mo
del emux


#these encoders require an "audioconvert" element:
#ENCODER_NEEDS_AUDIOCONVERT = ("flacenc", "wavpackenc")
#if this is lightweight enough, maybe we should include it unconditionally?
#SOURCE_NEEDS_AUDIOCONVERT = ("directsoundsrc", "osxaudiosrc", "autoaudiosrc", "wasapisrc")

#CUTTER_NEEDS_RESAMPLE = ("opusenc", )
#those don't work anyway:
#CUTTER_NEEDS_CONVERT = ("vorbisenc", "wavpackenc", "avenc_aac")
#ENCODER_CANNOT_USE_CUTTER = ("vorbisenc", "wavpackenc", "avenc_aac", "wavenc")


#options we use to tune for low latency:
OGG_DELAY = 20*MS_TO_NS
ENCODER_DEFAULT_OPTIONS_COMMON = {
            "lamemp3enc"    : {
                               "encoding-engine-quality" : 0,
                               },   #"fast"
            "wavpackenc"    : {
                               "mode"       : 1,        #"fast" (0 aka "very fast" is not supported)
                               "bitrate"    : 256000,
                               },
            "flacenc"       : {
                               "quality"    : 0,        #"fast"
                               },
            "avenc_aac"     : {
                               #"compliance" : 1,
                               "perfect-timestamp"  : 1,
                               },
            "faac"          : {
                               "perfect-timestamp"  : 1,
                               },
            #"vorbisenc"     : {"perfect-timestamp" : 1},
                           }
ENCODER_DEFAULT_OPTIONS = {
    "opusenc"       : {
        #only available with 1.6 onwards?
        "bitrate-type"   : 1,      #vbr
        "complexity"     : 0
        },
    }
#we may want to review this if/when we implement UDP transport:
MUXER_DEFAULT_OPTIONS = {
            "oggmux"        : {
                               "max-delay"          : OGG_DELAY,
                               "max-page-delay"     : OGG_DELAY,
                               },
            "webmmux"       : {
                               "writing-app"        : "Xpra",
                               "streamable"         : 1,
                               #"min-index-interval" : 0,
                               },
            "matroskamux"   : {
                               "writing-app"        : "Xpra",
                               "streamable"         : 1,
                },
            "mp4mux"        : {
                               "faststart"          : 1,
                               "streamable"         : 1,
                               "fragment-duration"  : 20,
                               "presentation-time"  : 0,
                               }
           }

#based on the encoder options above:
RECORD_PIPELINE_LATENCY = 25
ENCODER_LATENCY = {
        VORBIS      : 0,
        VORBIS_OGG  : 0,
        VORBIS_MKA  : 0,
        MP3         : 250,
        FLAC        : 50,
        WAV         : 0,
        WAVPACK     : 600,
        OPUS        : 0,
        SPEEX       : 0,
       }

CODEC_ORDER = [
    #best results:
    OPUS,
    #smooth playback but low compression:
    FLAC, WAVPACK, WAV_LZ4, WAV,
    #YMMV:
    OPUS_OGG, VORBIS_MKA, VORBIS_OGG, VORBIS,
    MP3, MP3_ID3V2, FLAC_OGG, AAC_MPEG4,
    SPEEX_OGG, VORBIS, OPUS_MKA, MP3_MPEG4,
    ]


def get_encoder_default_options(encoder):
    #strip the muxer:
    enc = encoder.split("+")[0]
    options = ENCODER_DEFAULT_OPTIONS_COMMON.get(enc, {}).copy()
    options.update(ENCODER_DEFAULT_OPTIONS.get(enc, {}))
    return options


CODECS = None
ENCODERS = {}       #(encoder, payloader, stream-compressor)
DECODERS = {}       #(decoder, depayloader, stream-compressor)

def get_encoders():
    init_codecs()
    return ENCODERS

def get_decoders():
    init_codecs()
    return DECODERS

def init_codecs():
    global CODECS
    if CODECS is not None or import_gst() is None:
        return CODECS or {}
    #populate CODECS:
    CODECS = {}
    for elements in CODEC_OPTIONS:
        if not validate_encoding(elements):
            continue
        try:
            encoding, encoder, payloader, decoder, depayloader, stream_compressor = (list(elements)+[None])[:6]
        except ValueError as e:
            log.error("Error: invalid codec entry: %s", e)
            log.error(" %s", elements)
            continue
        add_encoder(encoding, encoder, payloader, stream_compressor)
        add_decoder(encoding, decoder, depayloader, stream_compressor)
    log("initialized sound codecs:")
    def ci(v):
        return "%-22s" % (v or "")
    log("  - %s", "".join(ci(v) for v in ("encoder/decoder", "(de)payloader", "stream-compressor")))
    for k in CODEC_ORDER:
        if k in ENCODERS or k in DECODERS:
            CODECS[k] = True
            log("* %s :", k)
            if k in ENCODERS:
                log("  - %s", "".join([ci(v) for v in ENCODERS[k]]))
            if k in DECODERS:
                log("  - %s", "".join([ci(v) for v in DECODERS[k]]))
    return CODECS

def add_encoder(encoding, encoder, payloader, stream_compressor):
    if encoding in ENCODERS:
        return
    if OSX and encoding in (OPUS_OGG, ):
        log("avoiding %s on Mac OS X", encoding)
        return
    if has_plugins(encoder, payloader):
        ENCODERS[encoding] = (encoder, payloader, stream_compressor)

def add_decoder(encoding, decoder, depayloader, stream_compressor):
    if encoding in DECODERS:
        return
    if has_plugins(decoder, depayloader):
        DECODERS[encoding] = (decoder, depayloader, stream_compressor)

def validate_encoding(elements):
    #generic platform validation of encodings and plugins
    #full of quirks
    encoding = elements[0]
    if force_enabled(encoding):
        log.info("sound codec %s force enabled", encoding)
        return True
    if encoding in (VORBIS_OGG, VORBIS) and get_gst_version()<(1, 12):
        log("skipping %s - not sure which GStreamer versions support it", encoding)
        return False
    if encoding.startswith(OPUS):
        if encoding==OPUS_MKA and get_gst_version()<(1, 8):
            #this causes "could not link opusenc0 to webmmux0"
            #(not sure which versions are affected, but 1.8.x is not)
            log("skipping %s with GStreamer %s", encoding, get_gst_version())
            return False
    try:
        stream_compressor = elements[5]
    except IndexError:
        stream_compressor = None
    if stream_compressor and not has_stream_compressor(stream_compressor):
        log("skipping %s: missing %s", encoding, stream_compressor)
        return False
    return True

def has_stream_compressor(stream_compressor):
    if stream_compressor not in ("lz4", ):
        log.warn("Warning: invalid stream compressor '%s'", stream_compressor)
        return False
    from xpra.net.compression import use
    if stream_compressor=="lz4" and not use("lz4"):
        return False
    return True

def get_muxers():
    muxers = []
    for name,muxer,_ in MUX_OPTIONS:
        if has_plugins(muxer):
            muxers.append(name)
    return muxers

def get_demuxers():
    demuxers = []
    for name,_,demuxer in MUX_OPTIONS:
        if has_plugins(demuxer):
            demuxers.append(name)
    return demuxers

def get_stream_compressors():
    return [x for x in ("lz4", ) if has_stream_compressor(x)]

def get_encoder_elements(name):
    encoders = get_encoders()
    if name not in encoders:
        raise RuntimeError(f"invalid codec: {name} (should be one of: {encoders.keys()})")
    encoder, formatter, stream_compressor = encoders.get(name)
    if stream_compressor and not has_stream_compressor(stream_compressor):
        raise RuntimeError(f"stream-compressor {stream_compressor} not found")
    if encoder and not has_plugins(encoder):
        raise RuntimeError(f"encoder {encoder} not found")
    if formatter and not has_plugins(formatter):
        raise RuntimeError(f"formatter {formatter} not found")
    return encoder, formatter, stream_compressor

def get_decoder_elements(name):
    decoders = get_decoders()
    if name not in decoders:
        raise RuntimeError(f"invalid codec: {name} (should be one of: {decoders.keys()})")
    decoder, parser, stream_compressor = decoders.get(name)
    if stream_compressor and not has_stream_compressor(stream_compressor):
        raise RuntimeError(f"stream-compressor {stream_compressor} not found")
    if decoder and not has_plugins(decoder):
        raise RuntimeError(f"decoder {decoder} not found")
    if parser and not has_plugins(parser):
        raise RuntimeError(f"parser {parser} not found")
    return decoder, parser, stream_compressor

def has_encoder(name):
    encoders = get_encoders()
    if name not in encoders:
        return False
    encoder, fmt, _ = encoders.get(name)
    return has_plugins(encoder, fmt)

def has_decoder(name):
    decoders = get_decoders()
    if name not in decoders:
        return False
    decoder, parser, _ = decoders.get(name)
    return has_plugins(decoder, parser)

def has_codec(name):
    return has_encoder(name) and has_decoder(name)

def can_encode():
    return [x for x in CODEC_ORDER if has_encoder(x)]

def can_decode():
    return [x for x in CODEC_ORDER if has_decoder(x)]


def get_source_plugins():
    sources = []
    if POSIX and not OSX:
        try:
            from xpra.sound.pulseaudio.pulseaudio_util import has_pa
            #we have to put pulsesrc first if pulseaudio is installed
            #because using autoaudiosource does not work properly for us:
            #it may still choose pulse, but without choosing the right device.
            if has_pa():
                sources.append("pulsesrc")
        except ImportError as e:
            log("get_source_plugins() no pulsesrc: %s", e)
    if OSX:
        sources.append("osxaudiosrc")
    elif WIN32:
        sources.append("directsoundsrc")
        sources.append("wasapisrc")
    sources.append("autoaudiosrc")
    if POSIX:
        sources += ["alsasrc",
                    "osssrc", "oss4src",
                    "jackaudiosrc"]
    sources.append("audiotestsrc")
    return sources

def get_default_source():
    source = os.environ.get("XPRA_SOUND_SRC")
    sources = get_source_plugins()
    if source:
        if source not in sources:
            log.error("invalid default sound source: '%s' is not in %s", source, csv(sources))
        else:
            return source
    if POSIX and not OSX:
        try:
            from xpra.sound.pulseaudio.pulseaudio_util import has_pa, get_pactl_server
            if has_pa():
                s = get_pactl_server()
                if not s:
                    log("cannot connect to pulseaudio server?")
                else:
                    return "pulsesrc"
        except ImportError as e:
            log("get_default_source() no pulsesrc: %s", e)
    for source in sources:
        if has_plugins(source):
            return source
    return None

def get_sink_plugins():
    SINKS = []
    if OSX:
        SINKS.append("osxaudiosink")
    elif WIN32:
        SINKS.append("directsoundsink")
        SINKS.append("wasapisink")
    SINKS.append("autoaudiosink")
    if POSIX and not OSX:
        try:
            from xpra.sound.pulseaudio.pulseaudio_util import has_pa
            if has_pa():
                SINKS.append("pulsesink")
        except ImportError as e:
            log("get_sink_plugins() no pulsesink: %s", e)
    if POSIX:
        SINKS += ["alsasink", "osssink", "oss4sink", "jackaudiosink"]
    return SINKS

def get_default_sink_plugin():
    sink = os.environ.get("XPRA_SOUND_SINK")
    sinks = get_sink_plugins()
    if sink:
        if sink not in sinks:
            log.error("invalid default sound sink: '%s' is not in %s", sink, csv(sinks))
        else:
            return sink
    if POSIX and not OSX:
        try:
            from xpra.sound.pulseaudio.pulseaudio_util import has_pa, get_pactl_server
            if has_pa():
                s = get_pactl_server()
                if not s:
                    log("cannot connect to pulseaudio server?")
                else:
                    return "pulsesink"
        except ImportError as e:
            log("get_default_sink_plugin() no pulsesink: %s", e)
    for sink in sinks:
        if has_plugins(sink):
            return sink
    return None


def get_test_defaults(*_args):
    return  {"wave" : 2, "freq" : 110, "volume" : 0.4}

WARNED_MULTIPLE_DEVICES = False
def get_pulse_defaults(device_name_match=None, want_monitor_device=True,
                       input_or_output=None, remote=None, env_device_name=None):
    try:
        device = get_pulse_device(device_name_match, want_monitor_device, input_or_output, remote, env_device_name)
    except Exception as e:
        log("get_pulse_defaults%s",
            (device_name_match, want_monitor_device, input_or_output, remote, env_device_name), exc_info=True)
        log.warn("Warning: failed to identify the pulseaudio default device to use")
        log.warn(" %s", e)
        return {}
    if not device:
        return {}
    #make sure it is not muted:
    if POSIX and not OSX:
        try:
            from xpra.sound.pulseaudio.pulseaudio_util import has_pa, set_source_mute, set_sink_mute
            if has_pa():
                if input_or_output is True or want_monitor_device:
                    set_source_mute(device, mute=False)
                elif input_or_output is False:
                    set_sink_mute(device, mute=False)
        except Exception as e:
            log("device %s may still be muted: %s", device, e)
    return {"device" : bytestostr(device)}

def get_pulse_device(device_name_match=None, want_monitor_device=True,
                     input_or_output=None, remote=None, env_device_name=None):
    """
        choose the device to use
    """
    log("get_pulse_device%s", (device_name_match, want_monitor_device, input_or_output, remote, env_device_name))
    try:
        from xpra.sound.pulseaudio.pulseaudio_util import (
            has_pa, get_pa_device_options,
            get_default_sink, get_pactl_server,
            get_pulse_id,
            )
        if not has_pa():
            log.warn("Warning: pulseaudio is not available!")
            return None
    except ImportError as e:
        log.warn("Warning: pulseaudio is not available!")
        log.warn(" %s", e)
        return None
    pa_server = get_pactl_server()
    log("get_pactl_server()=%s", pa_server)
    if remote:
        log("start sound, remote pulseaudio server=%s, local pulseaudio server=%s", remote.pulseaudio_server, pa_server)
        #only worth comparing if we have a real server string
        #one that starts with {UUID}unix:/..
        if pa_server and pa_server.startswith("{") and \
            remote.pulseaudio_server and remote.pulseaudio_server==pa_server:
            log.error("Error: sound is disabled to prevent a sound loop")
            log.error(" identical Pulseaudio server '%s'", pa_server)
            return None
        pa_id = get_pulse_id()
        log("start sound, client id=%s, server id=%s", remote.pulseaudio_id, pa_id)
        if remote.pulseaudio_id and remote.pulseaudio_id==pa_id:
            log.error("Error: sound is disabled to prevent a sound loop")
            log.error(" identical Pulseaudio ID '%s'", pa_id)
            return None

    device_type_str = ""
    if input_or_output is not None:
        device_type_str = "input" if input_or_output else "output"
    if want_monitor_device:
        device_type_str += " monitor"
    #def get_pa_device_options(monitors=False, input_or_output=None, ignored_devices=["bell-window-system"])
    devices = get_pa_device_options(want_monitor_device, input_or_output)
    log("found %i pulseaudio %s device%s: %s", len(devices), device_type_str, engs(devices), devices)
    ignore = ()
    if input_or_output is True:
        ignore = IGNORED_INPUT_DEVICES
    elif input_or_output is False:
        ignore = IGNORED_OUTPUT_DEVICES
    else:
        ignore = IGNORED_INPUT_DEVICES+IGNORED_OUTPUT_DEVICES
    if ignore and devices:
        #filter out the ignore list:
        filtered = {}
        for k,v in devices.items():
            kl = bytestostr(k).strip().lower()
            vl = bytestostr(v).strip().lower()
            if kl not in ignore and vl not in ignore:
                filtered[k] = v
        devices = filtered

    if not devices:
        log.error("Error: sound forwarding is disabled")
        log.error(" could not detect any Pulseaudio %s devices", device_type_str)
        return None

    env_device = None
    if env_device_name:
        env_device = os.environ.get(env_device_name)
    #try to match one of the devices using the device name filters:
    if len(devices)>1:
        filters = []
        matches = []
        for match in (device_name_match, env_device):
            if not match:
                continue
            if match!=env_device:
                filters.append(match)
            match = match.lower()
            log("trying to match '%s' in devices=%s", match, devices)
            matches = dict((k,v) for k,v in devices.items()
                           if (bytestostr(k).strip().lower().find(match)>=0 or
                               bytestostr(v).strip().lower().find(match)>=0))
            #log("matches(%s, %s)=%s", devices, match, matches)
            if len(matches)==1:
                log("found name match for '%s': %s", match, tuple(matches.items())[0])
                break
            elif len(matches)>1:
                log.warn("Warning: Pulseaudio %s device name filter '%s'", device_type_str, match)
                log.warn(" matched %i devices", len(matches))
        if filters or matches:
            if not matches:
                log.warn("Warning: Pulseaudio %s device name filter%s:", device_type_str, engs(filters))
                log.warn(" %s", csv("'%s'" % x for x in filters))
                log.warn(" did not match any of the devices found:")
                for k,v in devices.items():
                    log.warn(" * '%s'", k)
                    log.warn("   '%s'", v)
                return None
            devices = matches

    #still have too many devices to choose from?
    if len(devices)>1:
        if want_monitor_device:
            #use the monitor of the default sink if we find it:
            default_sink = get_default_sink()
            default_monitor = default_sink+".monitor"
            if default_monitor in devices:
                device_name = devices.get(default_monitor)
                log.info("using monitor of default sink: %s", device_name)
                return default_monitor

        global WARNED_MULTIPLE_DEVICES
        if not WARNED_MULTIPLE_DEVICES:
            WARNED_MULTIPLE_DEVICES = True
            dtype = "audio"
            if want_monitor_device:
                dtype = "output monitor"
            elif input_or_output is False:
                dtype = "audio output"
            elif input_or_output is True:
                dtype = "audio input"
            log.info("found %i %s devices:", len(devices), dtype)
            for k,v in devices.items():
                log.info(" * %s", bytestostr(v))
                log.info("   %s", bytestostr(k))
            if not env_device: #used already!
                log.info(" to select a specific one,")
                log.info(" use the environment variable '%s'", env_device_name)
        #default to first one:
        if USE_DEFAULT_DEVICE:
            log.info("using default pulseaudio device")
            return None
    #default to first one:
    device, device_name = tuple(devices.items())[0]
    log.info("using pulseaudio device:")
    log.info(" '%s'", bytestostr(device_name))
    return device

def get_pulse_source_defaults(device_name_match=None, want_monitor_device=True, remote=None):
    return get_pulse_defaults(device_name_match, want_monitor_device,
                              input_or_output=not want_monitor_device, remote=remote,
                              env_device_name=XPRA_PULSE_SOURCE_DEVICE_NAME)

def get_pulse_sink_defaults():
    return get_pulse_defaults(want_monitor_device=False, input_or_output=False,
                              env_device_name=XPRA_PULSE_SINK_DEVICE_NAME)

def get_directsound_source_defaults(device_name_match=None, want_monitor_device=True, remote=None):
    try:
        from xpra.platform.win32.directsound import get_devices, get_capture_devices
        if not want_monitor_device:
            devices = get_devices()
            log("DirectSoundEnumerate found %i device%s", len(devices), engs(devices))
        else:
            devices = get_capture_devices()
            log("DirectSoundCaptureEnumerate found %i device%s", len(devices), engs(devices))
        names = []
        if devices:
            for guid, name in devices:
                if guid:
                    log("* %-32s %s", name, guid)
                else:
                    log("* %s", name)
                names.append(name)
            device_name = None
            if device_name_match:
                for name in names:
                    if name.lower().find(device_name_match)>=0:
                        device_name = name
                        break
            if device_name is None:
                for name in names:
                    if name.lower().find("primary")>=0:
                        device_name = name
                        break
            log("best matching %sdevice: %s", ["","capture "][want_monitor_device], device_name)
            if device_name is None and want_monitor_device:
                #we have to choose one because the default device
                #may not be a capture device?
                device_name = names[0]
            if device_name:
                log.info("using directsound %sdevice:", ["","capture "][want_monitor_device])
                log.info(" '%s'", device_name)
                return {
                        "device-name"   : device_name,
                        }
    except Exception as e:
        log("get_directsound_source_defaults%s", (device_name_match, want_monitor_device, remote), exc_info=True)
        log.error("Error quering sound devices:")
        log.estr(e)
    return {}


#a list of functions to call to get the plugin options
#at runtime (so we can perform runtime checks on remote data,
# to avoid sound loops for example)
DEFAULT_SRC_PLUGIN_OPTIONS = {
    "test"                  : get_test_defaults,
    "direct"                : get_directsound_source_defaults,
    }
DEFAULT_SINK_PLUGIN_OPTIONS = {}
if POSIX and not OSX:
    DEFAULT_SINK_PLUGIN_OPTIONS["pulse"] = get_pulse_sink_defaults
    DEFAULT_SRC_PLUGIN_OPTIONS["pulse"] = get_pulse_source_defaults



def get_sound_source_options(plugin, options_str, device, want_monitor_device, remote):
    """
        Given a plugin (short name), options string and remote info,
        return the options for the plugin given,
        using the dynamic defaults (which may use remote info)
        and applying the options string on top.
    """
    #ie: get_sound_source_options("audiotestsrc", "wave=4,freq=220", {remote_pulseaudio_server=XYZ}):
    #use the defaults as starting point:
    defaults_fn = DEFAULT_SRC_PLUGIN_OPTIONS.get(plugin)
    log("DEFAULT_SRC_PLUGIN_OPTIONS(%s)=%s", plugin, defaults_fn)
    if defaults_fn:
        options = defaults_fn(device, want_monitor_device, remote)
        log("%s%s=%s", defaults_fn, (device, want_monitor_device, remote), options)
        if options is None:
            #means failure
            return None
    else:
        options = {}
        #if we add support for choosing devices in the GUI,
        #this code will then get used:
        if device and plugin in SRC_HAS_DEVICE_NAME:
            #assume the user knows the "device-name"...
            #(since I have no idea where to get the "device" string)
            options["device-name"] = device
    options.update(parse_simple_dict(options_str))
    return options


def parse_sound_source(all_plugins, sound_source_plugin, device, want_monitor_device, remote):
    #format: PLUGINNAME:options
    #ie: test:wave=2,freq=110,volume=0.4
    #ie: pulse:device=device.alsa_input.pci-0000_00_14.2.analog-stereo
    plugin = sound_source_plugin.split(":")[0]
    options_str = (sound_source_plugin+":").split(":",1)[1].rstrip(":")
    simple_str = (plugin).lower().strip()
    if not simple_str:
        simple_str = get_default_source()
        if not simple_str:
            #choose the first one from
            options = [x for x in get_source_plugins() if x in all_plugins]
            if not options:
                log.error("no source plugins available")
                return None, {}
            log("parse_sound_source: no plugin specified, using default: %s", options[0])
            simple_str = options[0]
    for s in ("src", "sound", "audio"):
        if simple_str.endswith(s):
            simple_str = simple_str[:-len(s)]
    gst_sound_source_plugin = NAME_TO_SRC_PLUGIN.get(simple_str)
    if not gst_sound_source_plugin:
        log.error("unknown source plugin: '%s' / '%s'", simple_str, sound_source_plugin)
        return  None, {}
    log("parse_sound_source(%s, %s, %s) plugin=%s", all_plugins, sound_source_plugin, remote, gst_sound_source_plugin)
    options = get_sound_source_options(simple_str, options_str, device, want_monitor_device, remote)
    log("get_sound_source_options%s=%s", (simple_str, options_str, remote), options)
    if options is None:
        #means error
        return None, {}
    return gst_sound_source_plugin, options


def loop_warning_messages(mode="speaker"):
    return [
        "Cannot start %s forwarding:" % mode,
        "client and server environment are identical,",
        "this would be likely to create an audio feedback loop",
        #" use XPRA_ALLOW_SOUND_LOOP=1 to force enable it",
        ]


def main():
    from xpra.platform import program_context
    from xpra.log import enable_color
    with program_context("GStreamer-Info", "GStreamer Information"):
        enable_color()
        if "-v" in sys.argv or "--verbose" in sys.argv:
            log.enable_debug()
        import_gst()
        v = get_gst_version()
        if not v:
            print("no gstreamer version information")
        else:
            if v[-1]==0:
                v = v[:-1]
            gst_vinfo = ".".join((str(x) for x in v))
            print("Loaded Python GStreamer version %s for Python %s.%s" % (
                gst_vinfo, sys.version_info[0], sys.version_info[1])
            )
        apn = get_all_plugin_names()
        print("GStreamer plugins found: " + csv(apn))
        print("")
        print("GStreamer version: " + ".".join([str(x) for x in get_gst_version()]))
        print("PyGStreamer version: " + ".".join([str(x) for x in get_pygst_version()]))
        print("")
        encs = [x for x in CODEC_ORDER if has_encoder(x)]
        decs = [x for x in CODEC_ORDER if has_decoder(x)]
        print("encoders:           " + csv(encs))
        print("decoders:           " + csv(decs))
        print("muxers:             " + csv(get_muxers()))
        print("demuxers:           " + csv(get_demuxers()))
        print("stream compressors: " + csv(get_stream_compressors()))
        print("source plugins:     " + csv([x for x in get_source_plugins() if x in apn]))
        print("sink plugins:       " + csv([x for x in get_sink_plugins() if x in apn]))
        print("default sink:       " + str(get_default_sink_plugin()))


if __name__ == "__main__":
    main()
