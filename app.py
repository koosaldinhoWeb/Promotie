import os

from flask import Flask

from app_core import init_database
from app_routes import register_routes


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "development-secret-change-in-production")

init_database()
register_routes(app)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
