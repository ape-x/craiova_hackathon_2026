"""Prevent explicit Romanian package requests from reusing UK hotel inputs."""
import re
import unicodedata


SETUP_EXPLANATION = (
    "Pachetul OTP–Hotel Aro Palace–OTP necesită configurarea explicită în formularul "
    "Romanian coach + hotel: datele șederii, bagajele și marjele de conexiune. "
    "Configurația UK salvată nu a fost substituită. Nicio căutare de furnizor nu a fost pornită."
)


def romanian_hotel_requested(text: str) -> bool:
    # This is a bounded guard, not an interpreter or inferred spending authority.
    value = "".join(char for char in unicodedata.normalize("NFKD", text)
                    if not unicodedata.combining(char)).lower()
    return bool(re.search(
        r"\b(?:transfero|direct[\s-]*aeroport|aro[\s-]*palace|romanian\s+(?:coach\s*(?:\+|and|plus)\s*)?hotel|"
        r"hotel(?:ul|ului|uri)?\s+romanesc|(?:hotel(?:ul|ului|uri)?|cazare(?:a)?)\s+"
        r"(?:(?:in|din|la|at|near)\s+)?(?:romania|brasov))\b", value))


def romanian_hotel_instruction(text: str) -> bool:
    """A report quote must not conceal a separate package-selection instruction."""
    value = "".join(char for char in unicodedata.normalize("NFKD", text)
                    if not unicodedata.combining(char)).lower()
    return any(romanian_hotel_requested(clause) and re.search(
        r"\b(?:use|using|choose|select|include|prefer|book|foloseste|folosind|alege|selecteaza|rezerva)\b",
        clause) for clause in re.split(r"[.;!?\n]", value))
