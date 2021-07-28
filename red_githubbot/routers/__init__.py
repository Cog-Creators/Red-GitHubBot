import importlib
import pkgutil

from gidgethub import routing

gh_router = routing.Router()

# import all submodules in order to fill `gh_router` with all the routes
for loader, module_name, is_pkg in pkgutil.iter_modules(__path__, __name__ + "."):
    importlib.import_module(module_name)
