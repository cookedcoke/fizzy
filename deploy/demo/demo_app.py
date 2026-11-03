"""The demo server (see README.md): Fizzy without its background jobs, so it never calls Square."""
from fizzy.app import create_app

app = create_app(background=False)
