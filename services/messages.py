"""Discord text limits, including UTF-16 surrogate pairs and blank output."""
import discord


def split_message(text, limit=1900):
    remaining = str(text or "").strip()
    if not remaining:
        return ["這次沒有取得可顯示的內容，請稍後再試。"]
    chunks = []
    while remaining:
        units = 0
        end = 0
        for char in remaining:
            width = 2 if ord(char) > 0xFFFF else 1
            if units + width > limit:
                break
            units += width
            end += 1
        if end == len(remaining):
            chunks.append(remaining)
            break
        split_at = max(remaining.rfind("\n", 0, end), remaining.rfind(" ", 0, end))
        if split_at <= 0:
            split_at = end
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    return chunks


async def send_private(interaction, text, **kwargs):
    chunks = split_message(text)
    for index, chunk in enumerate(chunks):
        options = kwargs if index == 0 else {}
        if interaction.response.is_done():
            await interaction.followup.send(chunk, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(), **options)
        else:
            await interaction.response.send_message(chunk, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(), **options)
