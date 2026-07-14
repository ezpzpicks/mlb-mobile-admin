import hashlib
import hmac
import os

import streamlit as st

ADMIN_AUTH_QUERY_KEY = "ezpz_admin_auth"


def _get_admin_password() -> str:
    password = ""
    try:
        password = str(st.secrets.get("ADMIN_PASSWORD", "") or "")
    except Exception:
        password = ""
    if not password:
        password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        password = "admin"
        st.warning("No ADMIN_PASSWORD secret found. Temporary local password is: admin")
    return str(password)


def _admin_auth_token(password: str) -> str:
    secret_seed = os.environ.get("ADMIN_COOKIE_SECRET", "")
    if not secret_seed:
        try:
            secret_seed = str(st.secrets.get("ADMIN_COOKIE_SECRET", "") or "")
        except Exception:
            secret_seed = ""
    payload = f"{password}|{secret_seed}|ezpz-multi-sport-admin-v1"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_query_param(name: str) -> str:
    try:
        value = st.query_params.get(name, "")
    except Exception:
        try:
            value = st.experimental_get_query_params().get(name, [""])
        except Exception:
            value = ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _set_query_param(name: str, value: str) -> None:
    try:
        st.query_params[name] = value
    except Exception:
        try:
            params = st.experimental_get_query_params()
            params[name] = value
            st.experimental_set_query_params(**params)
        except Exception:
            pass


def _clear_query_param(name: str) -> None:
    try:
        if name in st.query_params:
            del st.query_params[name]
    except Exception:
        try:
            params = st.experimental_get_query_params()
            params.pop(name, None)
            st.experimental_set_query_params(**params)
        except Exception:
            pass


def require_admin_password(logo_file: str = "ezpz_logo.png") -> None:
    password = _get_admin_password()
    expected = _admin_auth_token(password)
    current = _get_query_param(ADMIN_AUTH_QUERY_KEY)

    if st.session_state.get("admin_authenticated"):
        if current != expected:
            _set_query_param(ADMIN_AUTH_QUERY_KEY, expected)
        return

    if current and hmac.compare_digest(current, expected):
        st.session_state["admin_authenticated"] = True
        return

    if os.path.exists(logo_file):
        st.image(logo_file, width=160)
    st.title("EZPZ Model Builder")
    st.caption("Private multi-sport admin platform")
    entered = st.text_input("Admin password", type="password")
    if st.button("Log in", type="primary", use_container_width=True):
        if entered == password:
            st.session_state["admin_authenticated"] = True
            _set_query_param(ADMIN_AUTH_QUERY_KEY, expected)
            st.rerun()
        else:
            _clear_query_param(ADMIN_AUTH_QUERY_KEY)
            st.error("Incorrect password.")
    st.stop()
