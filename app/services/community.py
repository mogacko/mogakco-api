from app.models import PostBoard, PostCategory


def validate_post_category(
    board: PostBoard, category: PostCategory | None
) -> None:
    if board is PostBoard.TALK and category is None:
        raise ValueError("talk posts require a category")
    if board is not PostBoard.TALK and category is not None:
        raise ValueError("only talk posts accept a category")
