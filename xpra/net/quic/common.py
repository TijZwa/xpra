import asyncio
from aioquic.h0.connection import H0_ALPN, H0Connection
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.quic.configuration import QuicConfiguration
from aioquic.asyncio import serve
from aioquic.quic.logger import QuicLogger

from xpra.net.quic.protocol import HttpServerProtocol
from xpra.log import Logger

class QuicCommon:
    def __init__(self):
        quiclogger = QuicLogger()
        self.configuration = QuicConfiguration(
            alpn_protocols=H3_ALPN + H0_ALPN + ["siduck"],
            is_client=False,
            max_datagram_frame_size=65536,
            quic_logger=quiclogger,
        )
        self.configuration.load_cert_chain("/opt/ssl/star_vpo_nl.pem", "/opt/ssl/star_vpo_nl.key")
        self.logger = Logger("network")

    def start(self):
        self.logger.info("Quic testserver starting")
        asyncio.run(self.doServe(), debug=True)

    async def doServe(self):
        self.logger.info("Quic testserver doServe")
        await serve(
            "0.0.0.0",
            10000,
            configuration=self.configuration,
            create_protocol=HttpServerProtocol,
        )
        await asyncio.Future()
        self.logger.info("Quic testserver end")