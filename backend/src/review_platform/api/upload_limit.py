"""Bound upload JSON before deserialization, including chunked requests."""
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

class UploadBodyLimitMiddleware:
    def __init__(self,app: ASGIApp,max_bytes: int=13_500_000) -> None:
        self.app,self.max_bytes=app,max_bytes

    async def __call__(self,scope: Scope,receive: Receive,send: Send) -> None:
        if scope['type']!='http' or scope.get('path')!='/api/v2/uploads':
            await self.app(scope,receive,send);return
        chunks=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            body=message.get('body',b'');size+=len(body)
            if size>self.max_bytes:
                await JSONResponse({'code':'file_too_large','message':'Допустим файл до 10 МБ.','action':None},status_code=413)(scope,receive,send);return
            chunks.append(body)
            if not message.get('more_body',False):break
        consumed=False
        async def buffered() -> Message:
            nonlocal consumed
            if not consumed:
                consumed=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        await self.app(scope,buffered,send)
