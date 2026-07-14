"""
주간 DM cog.

매주 토요일 18:00(KST)에 weekly_dm_settings에 등록된 제목/내용으로 서버 전 멤버에게
DM을 자동 발송합니다. /내용수정 명령어로 관리자가 제목/내용을 수정할 수 있습니다.

참고: 원본(cian24.py)에는 WeeklyDmEditModal이라는 별도 모달이 있었는데, 이건
weekly_dm_content라는(어디서도 생성되지 않는) 테이블에 저장하는 코드였고 실제로는
아무 버튼/명령어에서도 호출되지 않는 죽은 코드였습니다. 실사용되는 건 WeeklyDMModal
(weekly_dm_settings 테이블) 쪽이라 그것만 옮기고 죽은 코드는 제외했습니다.
"""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime

from config import KST, ADMIN_ROLE_ID
from db import get_db
from utils import schedule_ephemeral_delete

WEEKDAY_NAMES = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
WEEKDAY_CHOICES = [app_commands.Choice(name=n, value=i) for i, n in enumerate(WEEKDAY_NAMES)]


def _is_admin(user: discord.Member) -> bool:
    return bool(getattr(user.guild_permissions, 'administrator', False) or user.get_role(ADMIN_ROLE_ID))


class WeeklyDMModal(discord.ui.Modal, title='주간 DM 수정'):
    def __init__(self, t, m):
        super().__init__()
        self.t = discord.ui.TextInput(label='제목', default=t, required=True)
        self.m = discord.ui.TextInput(label='내용', style=discord.TextStyle.paragraph, default=m, required=True)
        self.add_item(self.t)
        self.add_item(self.m)

    async def on_submit(self, interaction: discord.Interaction):
        conn = get_db()
        conn.execute(
            """INSERT INTO weekly_dm_settings (guild_id, title, message, enabled)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(guild_id) DO UPDATE SET title=excluded.title, message=excluded.message, enabled=1""",
            (interaction.guild.id, self.t.value, self.m.value)
        )
        conn.commit()
        await interaction.response.send_message("주간 DM 설정 저장 완료!", ephemeral=True)
        schedule_ephemeral_delete(interaction)


class WeeklyDmCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.weekly_dm_loop.start()

    def cog_unload(self):
        self.weekly_dm_loop.cancel()

    @tasks.loop(minutes=1)
    async def weekly_dm_loop(self):
        now = datetime.now(KST)
        conn = get_db()
        rows = conn.execute(
            "SELECT guild_id, title, message, weekday, hour, minute FROM weekly_dm_settings WHERE enabled=1"
        ).fetchall()
        sent_users = set()
        for g_id, title, msg, weekday, hour, minute in rows:
            if now.weekday() != weekday or now.hour != hour or now.minute != minute:
                continue
            guild = self.bot.get_guild(g_id)
            if not guild:
                continue
            embed = discord.Embed(title=title, description=msg, color=0xf39c12)
            await guild.chunk()
            for m in guild.members:
                if m.bot or m.id in sent_users:
                    continue
                try:
                    await m.send(embed=embed)
                    sent_users.add(m.id)
                    await asyncio.sleep(0.1)
                except Exception:
                    sent_users.add(m.id)

    @weekly_dm_loop.before_loop
    async def before_weekly_dm_loop(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="내용수정", description="[관리자] 매주 자동 DM에 보낼 제목/내용을 수정합니다.")
    async def edit_weekly_dm(self, interaction: discord.Interaction):
        if not _is_admin(interaction.user):
            await interaction.response.send_message("관리자만 사용 가능합니다.", ephemeral=True)
            schedule_ephemeral_delete(interaction)
            return
        conn = get_db()
        row = conn.execute("SELECT title, message FROM weekly_dm_settings WHERE guild_id=?", (interaction.guild.id,)).fetchone()
        await interaction.response.send_modal(
            WeeklyDMModal(row[0] if row else "이번 주 길드 공지", row[1] if row else "내용을 입력하세요")
        )

    @app_commands.command(name="주간디엠시간설정", description="[관리자] 주간 DM을 발송할 요일과 시간을 설정합니다.")
    @app_commands.describe(요일="발송할 요일", 시="발송 시각 - 시 (0~23)", 분="발송 시각 - 분 (0~59)")
    @app_commands.choices(요일=WEEKDAY_CHOICES)
    async def set_weekly_dm_time(
        self,
        interaction: discord.Interaction,
        요일: app_commands.Choice[int],
        시: app_commands.Range[int, 0, 23],
        분: app_commands.Range[int, 0, 59],
    ):
        if not _is_admin(interaction.user):
            await interaction.response.send_message("관리자만 사용 가능합니다.", ephemeral=True)
            schedule_ephemeral_delete(interaction)
            return
        conn = get_db()
        conn.execute(
            """INSERT INTO weekly_dm_settings (guild_id, title, message, enabled, weekday, hour, minute)
               VALUES (?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET weekday=excluded.weekday, hour=excluded.hour, minute=excluded.minute""",
            (interaction.guild.id, "이번 주 길드 공지", "내용을 입력하세요", 요일.value, 시, 분)
        )
        conn.commit()
        await interaction.response.send_message(
            f"✅ 주간 DM 발송 시간이 **매주 {요일.name} {시:02d}:{분:02d}**(KST)로 설정되었습니다.", ephemeral=True
        )
        schedule_ephemeral_delete(interaction)

    @app_commands.command(name="주간디엠설정목록", description="[관리자] 현재 주간 DM 설정을 확인합니다.")
    async def view_weekly_dm_settings(self, interaction: discord.Interaction):
        if not _is_admin(interaction.user):
            await interaction.response.send_message("관리자만 사용 가능합니다.", ephemeral=True)
            schedule_ephemeral_delete(interaction)
            return
        conn = get_db()
        row = conn.execute(
            "SELECT title, message, enabled, weekday, hour, minute FROM weekly_dm_settings WHERE guild_id=?",
            (interaction.guild.id,),
        ).fetchone()

        embed = discord.Embed(title="📋 주간 DM 설정", color=0xf39c12)
        if not row:
            embed.description = "아직 설정된 내용이 없습니다. `/내용수정`으로 먼저 내용을 등록해주세요."
            embed.add_field(name="기본 발송 시간", value=f"매주 {WEEKDAY_NAMES[5]} 18:00 (KST)", inline=False)
        else:
            title, message, enabled, weekday, hour, minute = row
            embed.add_field(name="상태", value="🟢 활성화" if enabled else "🔴 비활성화", inline=True)
            embed.add_field(name="발송 시간", value=f"매주 {WEEKDAY_NAMES[weekday]} {hour:02d}:{minute:02d} (KST)", inline=True)
            embed.add_field(name="제목", value=title, inline=False)
            preview = message if len(message) <= 500 else message[:500] + "..."
            embed.add_field(name="내용", value=preview, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        schedule_ephemeral_delete(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(WeeklyDmCog(bot))
