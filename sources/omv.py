# OpenMediaVault, through a small agent on the host that serves disk usage as JSON
# (no authentication — keep it on the LAN).
#
#   LABHUD_OMV_URL            http://omv:9190/

from ._common import env, request, source


@source("omv", every=300, env=("OMV_URL",))
def omv():
    return request(env("OMV_URL"), timeout=8)
