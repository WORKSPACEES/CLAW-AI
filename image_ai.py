from urllib.parse import quote


def build_image_url(prompt):
    prompt = quote(prompt)

    return (
        f"https://image.pollinations.ai/prompt/{prompt}"
        "?width=1024"
        "&height=1024"
        "&nologo=true"
    )