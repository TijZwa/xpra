#
# demo application for http3_server.py
#

from starlette.types import Receive, Scope, Send

async def wt(scope: Scope, receive: Receive, send: Send) -> None:
    """
    WebTransport echo endpoint.
    """
    # accept connection
    message = await receive()
    assert message["type"] == "webtransport.connect"
    await send({"type": "webtransport.accept"})

    # echo back received data
    while True:
        message = await receive()
        if message["type"] == "webtransport.datagram.receive":
            await send(
                {
                    "data": message["data"],
                    "type": "webtransport.datagram.send",
                }
            )
        elif message["type"] == "webtransport.stream.receive":
            await send(
                {
                    "data": message["data"],
                    "stream": message["stream"],
                    "type": "webtransport.stream.send",
                }
            )

async def xpraApplicationscope(Scope, receive: Receive, send: Send) -> None:
    # accept connection
    message = await receive()
    assert message["type"] == "webtransport.connect"
    await send({"type": "webtransport.accept"})
    while True:
        message = await receive()
        ### we need to init the Xpra protocol here

async def app(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "webtransport" and scope["path"] == "/echotest":
        await wt(scope, receive, send)
    elif scope["type"] == "webtransport" and scope["path"] == "/xpra":
        await wt(scope, receive, send)