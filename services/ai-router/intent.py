"""Intent detection for routing messages to the appropriate model."""

import re

# Image generation patterns — "create/generate/draw/make" + "image/picture/photo/art"
_IMAGE_GEN_RE = re.compile(
    r"\b(?:create|generate|draw|make|paint|design|render|sketch)\b"
    r".*\b(?:image|picture|photo|art|illustration|portrait|logo|icon|wallpaper|poster)\b"
    r"|\b(?:image|picture|photo|art|illustration|portrait|logo|icon)\b"
    r".*\b(?:create|generate|draw|make|paint|design|render|sketch)\b"
    r"|\b(?:maak|teken|genereer)\b"  # Dutch variants
    r".*\b(?:foto|afbeelding|plaatje|tekening|beeld)\b",
    re.IGNORECASE,
)

# Code-related patterns
_CODE_RE = re.compile(
    r"\b(?:"
    r"code|program|script|function|class|method|variable|syntax|compile|runtime"
    r"|error|traceback|exception|stacktrace|bug|debug|fix\s+(?:the\s+)?(?:bug|error|issue)"
    r"|python|javascript|typescript|java|rust|golang|html|css|sql|regex|bash|shell|powershell"
    r"|react|vue|angular|django|flask|fastapi|node(?:js)?|npm|pip|cargo|docker"
    r"|api\s*endpoint|rest\s*api|http\s*request|json\s*schema|graphql"
    r"|algorithm|data\s*structure|sorting|recursion|loop|array|hashmap|linked\s*list"
    r"|git\s+(?:commit|push|pull|merge|rebase|branch|checkout)"
    r"|database|query|select\s+\w+\s+from|insert\s+into|create\s+table"
    r"|unit\s*test|pytest|jest|mock|assert"
    r"|import\s+\w+|def\s+\w+|class\s+\w+|function\s+\w+|const\s+\w+|let\s+\w+"
    r"|refactor|implement|coder?review"
    r")\b",
    re.IGNORECASE,
)


def detect_intent(message: str, has_images: bool = False) -> str:
    """Detect user intent from message text.

    Returns one of: 'vision', 'image_gen', 'code', 'general'.

    Checked in priority order:
    1. has_images → vision (user uploaded an image)
    2. image generation keywords → image_gen
    3. code/programming keywords → code
    4. everything else → general
    """
    if has_images:
        return "vision"

    if _IMAGE_GEN_RE.search(message):
        return "image_gen"

    if _CODE_RE.search(message):
        return "code"

    return "general"
