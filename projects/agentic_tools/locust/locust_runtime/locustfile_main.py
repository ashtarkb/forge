"""
Main Locust entry point — shared across all FORGE projects.

Activates the user class specified by USER_CLASS env var.
All files are mounted flat under /scripts/ in the Locust pods.

Environment Variables:
    USER_CLASS       - User class to activate (default: ResponsesSimpleUser)
    LOAD_SHAPE       - Load shape: steady | spike | realistic | poisson | custom
"""

import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

user_class_name = os.environ.get("USER_CLASS", "ResponsesSimpleUser")

_user_class_modules = [
    "responses_users",
    "mcp_session_user",
    "mcp_2026_user",
]


def _activate_user_class():
    """Find and activate the requested user class.

    Returns the class so it can be assigned to exactly ONE module-level name.
    All intermediate references (mod, cls) stay local to the function and
    never leak into the module namespace where Locust would discover them
    as duplicate User subclasses.
    """
    for module_name in _user_class_modules:
        try:
            mod = __import__(module_name)
            cls = getattr(mod, user_class_name, None)
            if cls:
                cls.abstract = False
                print(f"Active user class: {user_class_name}")
                return cls
        except ImportError:
            continue

    print(f"WARNING: Unknown USER_CLASS '{user_class_name}', attempting fallback")
    try:
        import responses_users

        responses_users.ResponsesSimpleUser.abstract = False
        print("Fallback: activated ResponsesSimpleUser")
        return responses_users.ResponsesSimpleUser
    except ImportError:
        print("ERROR: No user class modules found")
        return None


ActiveUserClass = _activate_user_class()
del _activate_user_class
