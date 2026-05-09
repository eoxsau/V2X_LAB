from app.services.mock_repository import get_routes


def get_candidate_routes() -> list[dict[str, object]]:
    return get_routes()
