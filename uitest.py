import discord
from discord.ext import commands

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

class TestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # 영구

    @discord.ui.button(label="테스트 버튼", style=discord.ButtonStyle.green, custom_id="test_persistent_btn")
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ 살아있음!", ephemeral=True)

@bot.event
async def setup_hook():
    bot.add_view(TestView())  # 전역 등록
    print("[DEBUG] TestView 등록 완료")

@bot.tree.command(name="테스트패널")
async def test_panel(interaction: discord.Interaction):
    await interaction.response.send_message("아래 버튼 눌러봐", view=TestView())

bot.run("11")