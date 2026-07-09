import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import sqlite3
import os

DB_PATH = "bdo_data.db"

# DB 함수
def _get_music_channel(guild_id: int):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT channel_id FROM music_channels WHERE guild_id=?", (guild_id,)).fetchone()
    conn.close()
    return row[0] if row else None

# yt-dlp 옵션 (쿠키 파일 설정 포함)
YTDL_OPTIONS = {
    # 오디오 전용 스트림이 없는 영상도 있으므로 fallback을 넉넉하게 둠
    'format': 'ba/bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt',
    # web 클라이언트만 사용: 쿠키 기반 인증을 실제로 활용해서 "Sign in to confirm you're not a bot" 회피
    # (android/ios 클라이언트는 쿠키를 쓰지 않아서 인증 없이 요청 → 봇 차단에 걸림)
    'extractor_args': {
        'youtube': {
            'player_client': ['web'],
        }
    },
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# 서버별 대기열 관리 딕셔너리
music_queues = {}

def play_next(client: discord.VoiceClient, guild_id: int, text_channel: discord.TextChannel, loop: asyncio.AbstractEventLoop):
    """곡 재생이 끝나면 자동으로 다음 곡을 틀어주는 콜백 함수"""
    if guild_id in music_queues and len(music_queues[guild_id]) > 0:
        track = music_queues[guild_id].pop(0)
        
        audio_source = discord.FFmpegPCMAudio(track['url'], **FFMPEG_OPTIONS)
        
        # 다음 곡 재생 (종료 시 다시 play_next 호출)
        client.play(audio_source, after=lambda e: play_next(client, guild_id, text_channel, loop))
        
        # 다음 곡 재생 알림 전송 (스레드 안전)
        embed = discord.Embed(
            title="🎵 지금 재생 중",
            description=f"**{track['title']}**\n요청: {track['requester']}",
            color=0x9b59b6
        )
        coro = text_channel.send(embed=embed, view=MusicControlView())
        asyncio.run_coroutine_threadsafe(coro, loop)
    else:
        # 대기열이 비었으면 봇 퇴장
        coro = client.disconnect()
        asyncio.run_coroutine_threadsafe(coro, loop)


class MusicControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⏸ 일시정지/재개", style=discord.ButtonStyle.secondary, custom_id="ytdl_pause")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = interaction.guild.voice_client
        if not voice_client:
            return await interaction.response.send_message("재생 중이 아닙니다.", ephemeral=True)
            
        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message("▶️ 재개했습니다.", ephemeral=True)
        elif voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("⏸ 일시정지했습니다.", ephemeral=True)

    @discord.ui.button(label="⏭ 다음 곡", style=discord.ButtonStyle.primary, custom_id="ytdl_skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = interaction.guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop() # stop()을 호출하면 자동적으로 after 콜백(play_next)이 실행됨
            await interaction.response.send_message("⏭ 다음 곡으로 넘어갑니다.", ephemeral=True)
        else:
            await interaction.response.send_message("재생 중이 아닙니다.", ephemeral=True)

    @discord.ui.button(label="⏹ 정지", style=discord.ButtonStyle.danger, custom_id="ytdl_stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        voice_client = interaction.guild.voice_client
        if voice_client:
            if interaction.guild.id in music_queues:
                music_queues[interaction.guild.id].clear()
            voice_client.stop()
            await voice_client.disconnect()
        await interaction.response.send_message("⏹ 재생을 종료하고 나갔습니다.", ephemeral=True)

    @discord.ui.button(label="📋 대기열", style=discord.ButtonStyle.secondary, custom_id="ytdl_queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        if guild_id not in music_queues or len(music_queues[guild_id]) == 0:
            return await interaction.response.send_message("대기열이 비어있습니다.", ephemeral=True)
            
        lines = []
        for i, track in enumerate(music_queues[guild_id][:10], 1):
            lines.append(f"{i}. {track['title']} ({track['requester']})")
            
        if len(music_queues[guild_id]) > 10:
            lines.append(f"...외 {len(music_queues[guild_id])-10}곡")
            
        embed = discord.Embed(title="📋 재생 대기열", description="\n".join(lines), color=0x3498db)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        music_ch = _get_music_channel(message.guild.id)
        if not music_ch or message.channel.id != music_ch:
            return

        query = message.content.strip()
        if not query:
            return

        if not message.author.voice or not message.author.voice.channel:
            return await message.channel.send(f"{message.author.mention} 먼저 음성 채널에 입장해주세요!", delete_after=10)

        search_msg = await message.channel.send(f"🔍 **{query}** 검색 중...")

        # yt-dlp로 비동기 검색 진행
        try:
            loop = self.bot.loop
            # URL인지 단순 검색어인지 판별
            search_query = query if query.startswith("http") else f"ytsearch:{query}"
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=False))
            
            if 'entries' in data:
                # 플레이리스트나 검색 결과일 경우 첫 번째 영상 선택
                data = data['entries'][0]
                
            track_info = {
                'title': data.get('title', '알 수 없는 제목'),
                'url': data.get('url'),
                'requester': message.author.display_name
            }
        except Exception as e:
            return await search_msg.edit(content=f"❌ 곡을 가져오는 중 오류가 발생했습니다: {e}")

        await search_msg.delete()

        # 음성 채널 접속
        voice_client = message.guild.voice_client
        if not voice_client:
            voice_client = await message.author.voice.channel.connect()

        # 대기열 딕셔너리 생성 (없을 경우)
        guild_id = message.guild.id
        if guild_id not in music_queues:
            music_queues[guild_id] = []

        # 큐에 추가
        music_queues[guild_id].append(track_info)

        # 현재 재생 중인 곡이 없으면 바로 재생 시작
        if not voice_client.is_playing() and not voice_client.is_paused():
            play_next(voice_client, guild_id, message.channel, self.bot.loop)
        else:
            # 이미 재생 중이면 대기열 추가 알림
            embed = discord.Embed(
                title="📋 대기열 추가",
                description=f"**{track_info['title']}**\n대기열 {len(music_queues[guild_id])}번째",
                color=0x2ecc71
            )
            await message.channel.send(embed=embed, delete_after=15)

    @app_commands.command(name="음악채널설정", description="[관리자] 노래봇이 사용할 텍스트 채널을 지정합니다")
    @app_commands.default_permissions(administrator=True)
    async def set_music_channel(self, interaction: discord.Interaction, channel: discord.TextChannel): # 텍스트 채널로 타입 변경
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO music_channels (guild_id, channel_id) VALUES (?,?)", (interaction.guild.id, channel.id))
        conn.commit()
        conn.close()
        await interaction.response.send_message(f"✅ 노래봇 채널이 {channel.mention}으로 설정되었습니다.", ephemeral=True)


async def setup(bot):
    # Cog 로드 전에 필수 라이브러리 체크 (안내용)
    import importlib.util
    if not importlib.util.find_spec("yt_dlp"):
        print("경고: yt_dlp 모듈이 설치되지 않았습니다. pip install yt-dlp 를 실행해주세요.")
    
    await bot.add_cog(MusicCog(bot))