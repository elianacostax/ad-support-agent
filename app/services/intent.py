import re

UNLOCK_KEYWORDS = (
    "desbloquear",
    "desbloquea",
    "desbloqueo",
    "cuenta bloqueada",
    "cuenta bloqueado",
    "me bloquearon",
)

RESET_KEYWORDS = (
    "contraseña",
    "contrasena",
    "password",
    "reset",
    "restablecer",
    "cambiar contraseña",
    "cambiar contrasena",
)

STATUS_KEYWORDS = (
    "estado",
    "consultar",
    "verificar usuario",
    "estado de la cuenta",
)


# Palabras que nunca deben tomarse como nombre de usuario.
_STOPWORDS = {
    # Artículos, preposiciones y pronombres
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "al", "a", "en", "con", "por", "para", "sin",
    "sobre", "entre", "hacia", "desde",
    "mi", "tu", "su", "mis", "tus", "sus", "nuestro", "nuestra",
    "yo", "me", "te", "se", "nos", "le", "les",
    "y", "o", "u", "ni", "que", "qué", "como", "cómo", "cuando",
    "cuándo", "donde", "dónde", "cual", "cuál", "cuales",
    "si", "no", "sí", "es", "son", "ser", "esta", "este", "esto",
    "está", "estan", "están", "hay",
    # Dominio
    "cuenta", "cuentas", "usuario", "usuarios", "user", "users",
    "red", "reds", "contraseña", "contrasena", "contraseñas",
    "contrasenas", "password", "passwords", "clave", "claves",
    "estado", "estados", "situacion", "situación",
    # Acciones y verbos frecuentes
    "ayudame", "ayúdame", "ayudar", "ayuda", "necesito", "quiero",
    "puedo", "puede", "favor", "porfa", "please",
    "cambiar", "cambia", "cambio", "cambiarme", "restablecer",
    "restablece", "restablecimiento", "reset", "resetear",
    "desbloquear", "desbloquea", "desbloqueo", "bloqueada",
    "bloqueado", "bloqueo", "consultar", "consulta", "verificar", "ver", "revisar",
    "revisa", "reviso", "mostrar", "muestrame",
    "dime", "diga", "indicame", "indica", "informa", "informar",
    "solicitar", "solicito", "solicita", "solicitud", "aprobar",
    "aprobacion", "aprobación", "aprueba",
    "debe", "deben", "deberia", "debería", "quedo", "quede",
    "queda", "tengo", "tiene",
    "nuevo", "nueva", "nuevos", "nuevas", "actual", "actualizar",
    "hola", "buenos", "buenas", "dias", "días", "tardes",
    "noches", "gracias", "hello", "hi", "saludo", "saludos",
    "tarjeta", "prueba", "test",
    # Rellenos frecuentes al final del mensaje
    "hoy", "ahora", "mañana", "ayer", "ya", "luego", "despues",
    "después", "possible", "posible",
}


# Patrones ordenados de mayor a menor precisión.
_USERNAME_PATTERNS = [
    # "del usuario sacosta", "de la cuenta sacosta", "al usuario sacosta"
    (
        r"\b(?:del|de\s+la|de|al|a|para)\s+(?:mi\s+)?(?:usuario|cuenta|user)\s+"
        r"(?:de\s+red\s+)?(?:de\s+)?(?:es\s+)?([a-zA-Z0-9_-]+)\b"
    ),
    # "usuario sacosta", "cuenta sacosta", "usuario de red sacosta",
    # "cuenta de susuga", "cuenta de red sacosta"
    (
        r"\b(?:usuario|cuenta|user)\s+(?:de\s+red\s+)?(?:de\s+)?"
        r"(?:es\s+)?([a-zA-Z0-9_-]+)\b"
    ),
    # "desbloquear la cuenta sacosta", "restablecer contraseña sacosta",
    # "cambiar la contraseña de sacosta"
    (
        r"\b(?:desbloquear|desbloquea|desbloqueo|restablecer|restablece|"
        r"cambiar|cambia|cambio)\s+(?:la\s+|el\s+|mi\s+)?"
        r"(?:cuenta|contraseña|contrasena|password|clave)\s+"
        r"(?:de\s+|del\s+|al\s+|para\s+)?([a-zA-Z0-9_-]+)\b"
    ),
    # "contraseña sacosta", "cuenta sacosta", "password sacosta"
    (
        r"\b(?:cuenta|contraseña|contrasena|password|clave)\s+"
        r"(?:de\s+|del\s+|al\s+|para\s+)?([a-zA-Z0-9_-]+)\b"
    ),
]


def detect_intent(message: str) -> str:
    text = message.casefold()

    if any(keyword in text for keyword in UNLOCK_KEYWORDS):
        return "unlock"

    if any(keyword in text for keyword in RESET_KEYWORDS):
        return "password_reset"

    if any(keyword in text for keyword in STATUS_KEYWORDS):
        return "status"

    return "other"


def _valid_candidate(token: str) -> bool:
    """Un candidato válido no es palabra vacía y usa formato de usuario."""
    if not token:
        return False

    if token.casefold() in _STOPWORDS:
        return False

    # Debe tener al menos 3 caracteres y solo caracteres de usuario.
    if len(token) < 3 or len(token) > 64:
        return False

    return bool(re.fullmatch(r"[a-zA-Z0-9_-]+", token))


def is_stopword(token: str) -> bool:
    """Indica si un token es una palabra vacía (nunca usuario)."""
    return token.casefold().strip() in _STOPWORDS


def extract_username(message: str) -> str | None:
    """
    Extrae el usuario de red de un mensaje, incluso cuando viene
    en el mismo mensaje que la petición.
    """
    # 1) Patrones específicos de "usuario/cuenta X".
    for pattern in _USERNAME_PATTERNS:
        for match in re.finditer(pattern, message, re.IGNORECASE):
            candidate = match.group(1)

            if _valid_candidate(candidate):
                return candidate

    # 2) Respaldo: si el mensaje contiene una intención reconocida,
    #    se toma el último token válido como usuario.
    #    Ej: "en que estado esta la cuenta de susuga" -> "susuga"
    #        "cambiar la contraseña de sacosta por favor" -> "sacosta"
    if detect_intent(message) != "other":
        tokens = re.findall(r"[\w-]+", message, re.UNICODE)

        for token in reversed(tokens):
            if _valid_candidate(token):
                return token

    return None
