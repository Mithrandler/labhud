"""What ⚙ > System > About and the sign-in dialog show beside the logo: the name, the version and
where the project lives. Public: nothing in it is secret, the page already shows the version."""

import os

from routes import route

LINKS = {"source": "https://github.com/Mithrandler/labhud",
         "app": "https://github.com/Mithrandler/labhud-android",
         "agent": "https://github.com/Mithrandler/labhud-agent"}


@route("GET", "/api/brand/about", guard="public")
def about(h, body):
    return 200, {"name": "LabHUD", "version": os.environ.get("LABHUD_VERSION", "dev"),
                 "logo": "/brand/lockup.svg", "mark": "/brand/mark.svg", "links": LINKS}
