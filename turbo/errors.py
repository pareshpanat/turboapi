class HTTPError(Exception):
    def __init__(self, status:int, message:str='Error', detail=None):
        super().__init__(message)
        self.status=int(status)
        self.message=str(message)
        self.detail=detail
