"""Owner-only Discord management. No HTTP server or extra admin token."""
import io
import json
import logging
import re
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands

from config import (MODEL_CHOICES, PERSONA_PATH, atomic_write_text,
                    load_persona_store, load_settings, save_persona_store, save_settings)
from services.messages import send_private

log = logging.getLogger(__name__)


class OwnerView(discord.ui.View):
    def __init__(self, bot, owner_id, timeout=600):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or not await self.bot.is_owner(interaction.user):
            await send_private(interaction, "只有開啟面板的 bot owner 可以操作。")
            return False
        return True

    async def on_error(self, interaction, error, item):
        log.exception("管理面板操作失敗", exc_info=error)
        text = str(error) if isinstance(error, ValueError) else "操作失敗，詳細資訊見伺服器日誌。"
        await send_private(interaction, text)


class OwnerModal(discord.ui.Modal):
    def __init__(self, bot, owner_id, **kwargs):
        super().__init__(**kwargs)
        self.bot = bot
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or not await self.bot.is_owner(interaction.user):
            await send_private(interaction, "只有 bot owner 可以操作。")
            return False
        return True

    async def on_error(self, interaction, error):
        log.exception("管理表單操作失敗", exc_info=error)
        await send_private(interaction, str(error) if isinstance(error, ValueError) else "操作失敗，請稍後再試。")


class SettingsModal(OwnerModal):
    def __init__(self, panel):
        super().__init__(panel.bot, panel.owner_id, title="YouTube 追蹤設定")
        settings = load_settings()
        self.channel = discord.ui.TextInput(label="YouTube 頻道 ID（UC 開頭）", default=settings["youtube_channel_id"], max_length=24)
        self.minutes = discord.ui.TextInput(label="檢查間隔（1～1440 分鐘）", default=str(settings["youtube_check_minutes"]), max_length=8)
        self.add_item(self.channel)
        self.add_item(self.minutes)

    async def on_submit(self, interaction):
        saved = save_settings({"youtube_channel_id": str(self.channel).strip(), "youtube_check_minutes": str(self.minutes).strip()})
        youtube = self.bot.get_cog("YouTubeTracker")
        if youtube:
            youtube.apply_loop_interval()
        await send_private(interaction, f"已儲存，間隔 {saved['youtube_check_minutes']:g} 分鐘。新追蹤頻道首次檢查會建立基準，不通知舊內容。")


class BudgetModal(OwnerModal):
    def __init__(self, panel):
        super().__init__(panel.bot, panel.owner_id, title="AI 回覆長度")
        self.budget = discord.ui.TextInput(label="回覆 token 上限（256～8192）", default=str(load_settings()["ai_max_output_tokens"]), max_length=4)
        self.add_item(self.budget)

    async def on_submit(self, interaction):
        saved = save_settings({"ai_max_output_tokens": str(self.budget).strip()})
        await send_private(interaction, f"回覆 token 上限已設為 {saved['ai_max_output_tokens']}，下次呼叫生效。")


class TextModal(OwnerModal):
    def __init__(self, panel, kind, content="", template_id=""):
        titles = {"default": "編輯預設人格", "summary": "編輯頻道記憶摘要", "template": "人格版型"}
        super().__init__(panel.bot, panel.owner_id, title=titles[kind])
        self.panel = panel
        self.channel_id = panel.channel_id
        self.kind = kind
        self.template_id = template_id
        self.original = content
        if len(content) > 4000:
            raise ValueError("內容超過 Discord 表單 4000 字限制。人格請用 /人格匯出 與 /人格匯入；記憶可先從面板匯出。")
        if kind == "template":
            old = load_persona_store()["templates"].get(template_id, {})
            self.original_template = old.copy()
            self.name_input = discord.ui.TextInput(label="版型名稱", default=old.get("name", ""), max_length=80)
            self.add_item(self.name_input)
        self.content_input = discord.ui.TextInput(label="內容", style=discord.TextStyle.paragraph, default=content, max_length=4000, required=kind != "summary")
        self.add_item(self.content_input)

    async def on_submit(self, interaction):
        text = str(self.content_input).strip()
        if self.kind == "default":
            current = PERSONA_PATH.read_text(encoding="utf-8") if PERSONA_PATH.exists() else ""
            if current != self.original:
                raise ValueError("人格在表單開啟後已變更，請重新開啟編輯。")
            atomic_write_text(PERSONA_PATH, text + "\n")
        elif self.kind == "template":
            store = load_persona_store()
            if store["templates"].get(self.template_id, {}) != self.original_template:
                raise ValueError("版型已被修改，請重新開啟。")
            self.template_id = self.template_id or f"persona-{uuid4().hex[:8]}"
            store["templates"][self.template_id] = {"name": str(self.name_input).strip(), "content": text}
            save_persona_store(store)
        else:
            ai = self.panel.ai()
            # If a background summary completed while the modal was open, do not overwrite it.
            async with ai.channel_locks[self.channel_id]:
                if ai.get_summary(self.channel_id) != self.original:
                    raise ValueError("摘要在表單開啟後已變更，請重新開啟。")
                ai.save_summary(self.channel_id, text)
        await send_private(interaction, "已儲存，下次使用立即生效。" + (f" 版型 ID：{self.template_id}" if self.template_id else ""))


class LabModal(OwnerModal):
    def __init__(self, panel):
        super().__init__(panel.bot, panel.owner_id, title="測試此頻道人格（不寫入記憶）")
        self.panel = panel
        self.channel_id = panel.channel_id
        self.prompt = discord.ui.TextInput(label="測試訊息", style=discord.TextStyle.paragraph, max_length=1500)
        self.add_item(self.prompt)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ai = self.panel.ai()
        instruction, _ = ai.get_persona_for_channel(self.channel_id)
        response = await ai.generate(instruction, str(self.prompt))
        await send_private(interaction, response.text)


class ConfirmView(OwnerView):
    def __init__(self, panel, action, template_id=""):
        super().__init__(panel.bot, panel.owner_id, timeout=60)
        self.panel = panel
        self.channel_id = panel.channel_id
        self.action = action
        self.template_id = template_id

    @discord.ui.button(label="確認清除", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        if self.action == "memory":
            await self.panel.ai().clear_channel_memory(self.channel_id)
        else:
            store = load_persona_store()
            store["templates"].pop(self.template_id, None)
            store["channel_personas"] = {k: v for k, v in store["channel_personas"].items() if v != self.template_id}
            save_persona_store(store)
        self.stop()
        await interaction.edit_original_response(content="已清除。", view=None)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="已取消。", view=None)


class ModelSelect(discord.ui.Select):
    def __init__(self):
        current = load_settings()["gemini_model"]
        super().__init__(placeholder="選擇 Gemini 模型", options=[discord.SelectOption(label=model, value=model, default=model == current) for model in MODEL_CHOICES])

    async def callback(self, interaction):
        save_settings({"gemini_model": self.values[0]})
        await send_private(interaction, f"已切換成 {self.values[0]}，下次呼叫生效。")


class ModelView(OwnerView):
    def __init__(self, panel):
        super().__init__(panel.bot, panel.owner_id)
        self.add_item(ModelSelect())


class RolePicker(discord.ui.RoleSelect):
    def __init__(self, panel):
        super().__init__(placeholder="選擇通知時要標記的身分組", min_values=1, max_values=1)
        self.panel = panel

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        destination = await self.panel.notification_destination()
        if destination.guild.id != interaction.guild_id:
            raise ValueError("通知頻道位於其他伺服器，請在那個伺服器開啟 /管理。")
        role = self.values[0]
        if role.is_default():
            raise ValueError("不能設定 @everyone 為通知身分組。")
        save_settings({"discord_role_id": role.id})
        await send_private(interaction, f"已設定通知身分組 <@&{role.id}>。")


class RoleView(OwnerView):
    def __init__(self, panel):
        super().__init__(panel.bot, panel.owner_id)
        self.add_item(RolePicker(panel))

    @discord.ui.button(label="取消身分組標記", style=discord.ButtonStyle.secondary)
    async def clear_role(self, interaction, button):
        save_settings({"discord_role_id": 0})
        await send_private(interaction, "通知不再標記身分組。")


class TemplateSelect(discord.ui.Select):
    def __init__(self, picker):
        self.picker = picker
        store = load_persona_store()
        keys = sorted(store["templates"])[picker.page * 25:(picker.page + 1) * 25]
        super().__init__(placeholder="選擇人格版型", disabled=not keys, options=[discord.SelectOption(label=str(store["templates"][key].get("name", key))[:100], value=key, description=key[:100]) for key in keys] or [discord.SelectOption(label="目前沒有版型", value="empty")])

    async def callback(self, interaction):
        self.picker.template_id = self.values[0]
        await interaction.response.edit_message(content=f"已選擇 `{self.values[0]}`；目標 <#{self.picker.channel_id}>。", view=self.picker)


class TemplateView(OwnerView):
    def __init__(self, panel):
        super().__init__(panel.bot, panel.owner_id)
        self.panel = panel
        self.channel_id = panel.channel_id
        self.page = 0
        self.template_id = ""
        self.refresh_select()

    def refresh_select(self):
        for item in list(self.children):
            if isinstance(item, TemplateSelect):
                self.remove_item(item)
        self.add_item(TemplateSelect(self))

    def selected(self):
        template = load_persona_store()["templates"].get(self.template_id)
        if not template:
            raise ValueError("請先選擇仍存在的人格版型。")
        return template

    @discord.ui.button(label="指派給目標頻道", style=discord.ButtonStyle.primary, row=1)
    async def assign(self, interaction, button):
        self.selected()
        store = load_persona_store()
        store["channel_personas"][str(self.channel_id)] = self.template_id
        save_persona_store(store)
        await send_private(interaction, f"<#{self.channel_id}> 已指派 `{self.template_id}`。原本的頻道記憶會保留。")

    @discord.ui.button(label="編輯", style=discord.ButtonStyle.secondary, row=1)
    async def edit(self, interaction, button):
        # A modal must capture this view's target rather than a later panel selection.
        proxy = TargetPanel(self.panel, self.channel_id)
        await interaction.response.send_modal(TextModal(proxy, "template", self.selected().get("content", ""), self.template_id))

    @discord.ui.button(label="刪除", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction, button):
        self.selected()
        await send_private(interaction, f"刪除 `{self.template_id}` 也會解除使用它的所有頻道指派。", view=ConfirmView(self.panel, "template", self.template_id))

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=2)
    async def previous(self, interaction, button):
        self.page = max(0, self.page - 1)
        self.refresh_select()
        await interaction.response.edit_message(content=f"人格版型，第 {self.page + 1} 頁。", view=self)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=2)
    async def next_page(self, interaction, button):
        count = len(load_persona_store()["templates"])
        self.page = min(max(0, (count - 1) // 25), self.page + 1)
        self.refresh_select()
        await interaction.response.edit_message(content=f"人格版型，第 {self.page + 1} 頁。", view=self)


class TargetPanel:
    def __init__(self, panel, channel_id):
        self.bot = panel.bot
        self.owner_id = panel.owner_id
        self.channel_id = channel_id

    def ai(self):
        cog = self.bot.get_cog("AIChat")
        if not cog:
            raise ValueError("AI Chat 尚未載入。")
        return cog


class TargetSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="選擇人格與記憶的目標頻道", channel_types=[discord.ChannelType.text], row=0)

    async def callback(self, interaction):
        self.view.channel_id = self.values[0].id
        await interaction.response.edit_message(embed=self.view.embed(), view=self.view)


class ActionSelect(discord.ui.Select):
    def __init__(self):
        actions = [("youtube", "YouTube 頻道與檢查間隔"), ("destination", "將目標頻道設為通知頻道"), ("role", "通知身分組"), ("posts", "開啟／關閉貼文通知"), ("model", "切換 Gemini 模型"), ("search", "開啟／關閉 AI 搜尋"), ("budget", "AI 回覆長度"), ("default", "編輯預設人格"), ("new_template", "新增人格版型"), ("templates", "版型管理與指派"), ("reset_persona", "目標頻道恢復預設人格"), ("lab", "測試目標頻道人格"), ("memory", "查看目標頻道記憶"), ("summary", "編輯目標頻道摘要"), ("export_memory", "匯出目標頻道完整記憶"), ("clear_memory", "清除目標頻道記憶"), ("reload_ai", "重新載入 AI Chat"), ("reload_youtube", "重新載入 YouTube")]
        super().__init__(placeholder="選擇管理操作", row=1, options=[discord.SelectOption(label=label, value=value) for value, label in actions])

    async def callback(self, interaction):
        await self.view.run_action(interaction, self.values[0])


class AdminPanel(OwnerView):
    def __init__(self, bot, owner_id, channel_id):
        super().__init__(bot, owner_id)
        self.channel_id = channel_id
        self.add_item(TargetSelect())
        self.add_item(ActionSelect())

    def ai(self):
        return TargetPanel(self, self.channel_id).ai()

    def youtube(self):
        cog = self.bot.get_cog("YouTubeTracker")
        if not cog:
            raise ValueError("YouTube 尚未載入。")
        return cog

    async def notification_destination(self):
        channel_id = load_settings()["discord_channel_id"]
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("通知頻道不是文字頻道。")
        return channel

    def embed(self):
        settings = load_settings()
        ai = self.bot.get_cog("AIChat")
        youtube = self.bot.get_cog("YouTubeTracker")
        embed = discord.Embed(title="Vibe Bot 管理", description=f"人格／記憶目標：<#{self.channel_id}>\n先選目標頻道，再選操作。面板有效 10 分鐘，過期請重新輸入 /管理。", color=0x5865F2)
        embed.add_field(name="通知", value=f"頻道 <#{settings['discord_channel_id']}>\n身分組 {('<@&' + str(settings['discord_role_id']) + '>') if settings['discord_role_id'] else '無'}\nYouTube `{settings['youtube_channel_id']}`\n間隔 {settings['youtube_check_minutes']:g} 分鐘／貼文 {'開' if settings['youtube_posts_enabled'] else '關'}", inline=False)
        embed.add_field(name="AI", value=f"`{settings['gemini_model']}`\n搜尋 {'開' if settings['ai_search_enabled'] else '關'}／回覆最多 {settings['ai_max_output_tokens']} tokens\n金鑰 {'已設定' if ai and ai.client else '未設定'}／最近錯誤 {ai.last_error if ai and ai.last_error else '無'}", inline=False)
        try:
            persona = load_persona_store()["channel_personas"].get(str(self.channel_id), "預設人格")
        except ValueError:
            persona = "personas.json 無法讀取"
        embed.add_field(name="目標頻道人格", value=str(persona)[:1000], inline=False)
        if youtube:
            status = []
            for source, label in (("videos", "影片"), ("posts", "貼文")):
                health = youtube.health[source]
                status.append(f"{label}：{health['error'] or ('已成功檢查' if health['last_success'] else '尚未檢查')}\n上次成功：{health['last_success'] or '無'}")
            embed.add_field(name="巡邏狀態", value="\n".join(status)[:1024], inline=False)
        return embed

    async def run_action(self, interaction, action):
        if action == "youtube":
            return await interaction.response.send_modal(SettingsModal(self))
        if action == "budget":
            return await interaction.response.send_modal(BudgetModal(self))
        if action == "default":
            content = PERSONA_PATH.read_text(encoding="utf-8") if PERSONA_PATH.exists() else ""
            return await interaction.response.send_modal(TextModal(self, "default", content))
        if action == "new_template":
            return await interaction.response.send_modal(TextModal(self, "template"))
        if action == "templates":
            return await send_private(interaction, f"人格版型管理；目標 <#{self.channel_id}>。", view=TemplateView(self))
        if action == "summary":
            return await interaction.response.send_modal(TextModal(self, "summary", self.ai().get_summary(self.channel_id)))
        if action == "lab":
            return await interaction.response.send_modal(LabModal(self))
        if action == "clear_memory":
            return await send_private(interaction, f"確定清除 <#{self.channel_id}> 的全部近期對話與摘要？", view=ConfirmView(self, "memory"))
        if action == "model":
            return await send_private(interaction, "選擇下次呼叫使用的模型。搜尋另計費；3.8 是預設。", view=ModelView(self))
        if action == "role":
            return await send_private(interaction, "選擇通知身分組。Bot 需要標記該身分組的權限。", view=RoleView(self))
        if action == "memory":
            return await self.ai().send_memory(interaction, self.channel_id)
        if action in ("posts", "search"):
            key = "youtube_posts_enabled" if action == "posts" else "ai_search_enabled"
            settings = load_settings()
            save_settings({key: not settings[key]})
            return await interaction.response.edit_message(embed=self.embed(), view=self)
        if action == "reset_persona":
            store = load_persona_store()
            store["channel_personas"].pop(str(self.channel_id), None)
            save_persona_store(store)
            return await interaction.response.edit_message(embed=self.embed(), view=self)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if action == "destination":
            channel = self.bot.get_channel(self.channel_id) or await self.bot.fetch_channel(self.channel_id)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild_id:
                raise ValueError("請選擇目前伺服器的文字頻道。")
            permissions = channel.permissions_for(channel.guild.me)
            if not permissions.send_messages or not permissions.view_channel or not permissions.embed_links:
                raise ValueError("Bot 在該頻道需要檢視頻道、傳送訊息與嵌入連結權限。")
            save_settings({"discord_channel_id": channel.id, "discord_role_id": 0})
            await send_private(interaction, f"通知改到 <#{channel.id}>。請重新選擇通知身分組。")
        elif action == "export_memory":
            memory = self.ai().memory
            with memory.connect() as conn:
                conn.execute("BEGIN")
                rows = conn.execute("SELECT id, user_id, message, timestamp FROM history WHERE channel_id=? ORDER BY id", (self.channel_id,)).fetchall()
                summary = conn.execute("SELECT summary_text FROM summaries WHERE channel_id=?", (self.channel_id,)).fetchone()
            payload = {"channel_id": str(self.channel_id), "summary": summary[0] if summary else "", "history": [dict(zip(("id", "user_id", "message", "timestamp"), row)) for row in rows]}
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            if len(content) > 8 * 1024 * 1024:
                raise ValueError("匯出內容超過 8 MiB，請從伺服器備份 SQLite 檔案。")
            await send_private(interaction, "頻道記憶匯出（含未壓縮對話與摘要）。", file=discord.File(io.BytesIO(content), filename=f"memory-{self.channel_id}.json"))
        elif action.startswith("reload_"):
            extension = "ai_chat" if action == "reload_ai" else "youtube"
            await self.bot.reload_extension(f"cogs.{extension}")
            await self.bot.tree.sync()
            await send_private(interaction, f"已重新載入 {extension}。")

    @discord.ui.button(label="刷新狀態", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction, button):
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="立即檢查影片與貼文", style=discord.ButtonStyle.primary, row=2)
    async def check(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.youtube().check_all_once()
        text = "\n".join(f"{'影片' if source == 'videos' else '貼文'}：{value.get('error') or value.get('note') or ('已通知 ' + str(value['sent']) + ' 則／讀到 ' + str(value['visible']) + ' 則')}" for source, value in result.items())
        await send_private(interaction, text)


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def owner(self, interaction):
        if not await self.bot.is_owner(interaction.user):
            await send_private(interaction, "只有 bot owner 可以操作管理功能。")
            return False
        return True

    @app_commands.command(name="管理", description="Owner：在 Discord 內管理通知、人格、模型與記憶。")
    @app_commands.describe(channel="人格與記憶的目標頻道；省略使用目前頻道")
    @app_commands.rename(channel="頻道")
    @app_commands.guild_only()
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if not await self.owner(interaction):
            return
        panel = AdminPanel(self.bot, interaction.user.id, channel.id if channel else interaction.channel_id)
        await interaction.response.send_message(embed=panel.embed(), view=panel, ephemeral=True)

    @app_commands.command(name="人格匯出", description="Owner：下載目標頻道正在使用的人格，或指定版型。")
    @app_commands.rename(channel="頻道", template_id="版型id", default="預設")
    @app_commands.describe(default="直接匯出預設人格，不使用頻道指派")
    async def export_persona(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None, template_id: str | None = None, default: bool = False):
        if not await self.owner(interaction):
            return
        ai = self.bot.get_cog("AIChat")
        if default and template_id:
            return await send_private(interaction, "請選擇預設人格或版型 ID 其中一項。")
        if default:
            content = ai.read_default_persona()
        elif template_id:
            template = load_persona_store()["templates"].get(template_id)
            if not template:
                return await send_private(interaction, "找不到該版型。")
            content = template.get("content", "")
        else:
            content, _ = ai.get_persona_for_channel(channel.id if channel else interaction.channel_id)
        await send_private(interaction, "人格內容：", file=discord.File(io.BytesIO(content.encode("utf-8")), filename="persona.md"))

    @app_commands.command(name="人格匯入", description="Owner：匯入 UTF-8 人格檔案，省略版型 ID 時更新預設人格。")
    @app_commands.rename(file="檔案", template_id="版型id", name="名稱")
    @app_commands.describe(template_id="指定版型 ID 會新增或更新該版型；省略更新預設人格", name="版型顯示名稱")
    async def import_persona(self, interaction: discord.Interaction, file: discord.Attachment, template_id: str | None = None, name: str | None = None):
        if not await self.owner(interaction):
            return
        if file.size > 64 * 1024 or not file.filename.lower().endswith((".md", ".txt")):
            return await send_private(interaction, "請使用 64 KiB 以內的 UTF-8 .md 或 .txt 檔案。")
        if template_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", template_id):
            return await send_private(interaction, "版型 ID 只能使用 1～80 個英文字母、數字、底線或連字號。")
        if name and len(name) > 80:
            return await send_private(interaction, "名稱最多 80 字。")
        await interaction.response.defer(ephemeral=True, thinking=True)
        raw = await file.read()
        if len(raw) > 64 * 1024:
            return await send_private(interaction, "檔案超過 64 KiB。")
        try:
            content = raw.decode("utf-8-sig").strip()
        except UnicodeDecodeError:
            return await send_private(interaction, "檔案不是 UTF-8，請轉換編碼後重試。")
        if not content:
            return await send_private(interaction, "人格內容不能為空。")
        if template_id:
            store = load_persona_store()
            old = store["templates"].get(template_id, {})
            store["templates"][template_id] = {"name": name or old.get("name", template_id), "content": content}
            save_persona_store(store)
        else:
            atomic_write_text(PERSONA_PATH, content + "\n")
        await send_private(interaction, f"已更新 {'版型 ' + template_id if template_id else '預設人格'}，下次聊天生效。")


async def setup(bot):
    await bot.add_cog(Admin(bot))
