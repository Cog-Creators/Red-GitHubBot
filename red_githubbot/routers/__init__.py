import importlib
import pkgutil

from gidgethub import routing

DISABLED_ROUTERS = {"fix_committed_and_released"}

gh_router = routing.Router()

# import all submodules in order to fill `gh_router` with all the routes
for loader, module_name, is_pkg in pkgutil.iter_modules(__path__, __name__ + "."):
    _, _, router_name = module_name.rpartition(".")
    if router_name not in DISABLED_ROUTERS:
        importlib.import_module(module_name)
