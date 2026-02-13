from turbo.routing import Router

def test_router_param():
    r=Router()
    async def h(): ...
    r.add('GET','/users/{id}',h)
    m=r.match('GET','/users/123')
    assert m and m.params['id']=='123'
