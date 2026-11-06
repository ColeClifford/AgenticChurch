from fastapi import APIRouter

from src import liturgy, release

router = APIRouter(prefix="/api")


@router.get("/info")
async def info():
    """Which commit of the source is running, with a link to it."""
    return release.info()


@router.get("/changelog")
async def changelog():
    return liturgy.reading_of_the_week()


@router.get("/sermon")
async def sermon():
    return liturgy.sermon_of_the_week()


@router.get("/hymn")
async def hymn():
    hymns = liturgy.hymns()
    primary = hymns[0]
    alternate = hymns[1] if len(hymns) > 1 else hymns[0]
    return {
        "primary": {"title": primary["title"], "text": primary["text"]},
        "alternate": {"title": alternate["title"], "text": alternate["text"]},
    }


@router.get("/creed")
async def creed():
    return {
        "title": "The Seven Commits",
        "commits": [{"number": i, "name": n, "text": t} for i, (n, t) in enumerate(liturgy.SEVEN_COMMITS, 1)],
    }
