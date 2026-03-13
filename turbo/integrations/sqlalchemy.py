from __future__ import annotations

from typing import Any, Callable

from ..deps import Depends
from ..request import Request


def create_sqlalchemy_engine(url: str, **kwargs):
    from sqlalchemy import create_engine  # type: ignore

    return create_engine(url, **kwargs)


def make_sqlalchemy_session_dependency(*, session_factory_key: str = "db_session_factory", commit_on_exit: bool = False):
    async def _dep(req: Request):
        factory = req.app.state.get(session_factory_key)
        if factory is None:
            raise RuntimeError(f"Missing app.state.{session_factory_key}")
        session = factory()
        failed = False
        try:
            yield session
        except Exception:
            failed = True
            if hasattr(session, "rollback"):
                session.rollback()
            raise
        finally:
            if commit_on_exit and not failed and hasattr(session, "commit"):
                session.commit()
            if hasattr(session, "close"):
                session.close()

    return Depends(_dep)


def register_sqlalchemy(
    app,
    url: str,
    *,
    engine_key: str = "db_engine",
    session_factory_key: str = "db_session_factory",
    create_engine_fn: Callable[..., Any] | None = None,
    sessionmaker_fn: Callable[..., Any] | None = None,
    **engine_kwargs,
):
    def _create(_app):
        if create_engine_fn is None:
            from sqlalchemy import create_engine as _create_engine  # type: ignore

            engine = _create_engine(url, **engine_kwargs)
        else:
            engine = create_engine_fn(url, **engine_kwargs)
        if sessionmaker_fn is None:
            from sqlalchemy.orm import sessionmaker  # type: ignore

            maker = sessionmaker(bind=engine)
        else:
            maker = sessionmaker_fn(bind=engine)
        return {"engine": engine, "session_factory": maker}

    async def _factory(a):
        bundle = _create(a)
        setattr(a.state, engine_key, bundle["engine"])
        return bundle["session_factory"]

    async def _cleanup(factory, a):
        engine = a.state.get(engine_key)
        if engine is not None and hasattr(engine, "dispose"):
            engine.dispose()
        try:
            delattr(a.state, engine_key)
        except Exception:
            pass

    app.add_state_resource(session_factory_key, _factory, cleanup=_cleanup)
