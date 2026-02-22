import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
import random
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Настройки для YouTube DL
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.uploader = data.get('uploader')
        self.duration = data.get('duration')
        self.like_count = data.get('like_count')
        self.view_count = data.get('view_count')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            # Берем первый элемент из плейлиста
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# Настройки бота
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Структуры данных для каждого сервера
queues = {}  # Очередь воспроизведения
history = {}  # История проигранных треков
radio_mode = {}  # Режим радио (вкл/выкл)
current_song = {}  # Текущий играющий трек

@bot.event
async def on_ready():
    print(f'{bot.user} подключился к Discord!')
    print(f'Бот находится на {len(bot.guilds)} серверах')
    await bot.change_presence(activity=discord.Game(name="!help | Музыка"))

def get_related_search(query):
    """Генерирует поисковый запрос для похожих песен"""
    related_queries = [
        f"похожие на {query}",
        f"как {query}",
        f"рекомендации как {query}",
        f"{query} похожие треки",
        f"в стиле {query}"
    ]
    return random.choice(related_queries)

async def get_recommendations(ctx, original_song):
    """Получает рекомендации на основе текущего трека"""
    try:
        # Формируем поисковый запрос на основе названия и исполнителя
        search_query = f"{original_song.title} {original_song.uploader} похожие песни"
        
        # Ищем похожую песню
        player = await YTDLSource.from_url(f"ytsearch:{search_query}", loop=bot.loop, stream=True)
        
        # Проверяем, чтобы не зациклиться на той же песне
        if player.title.lower() == original_song.title.lower():
            # Если нашлась та же песня, пробуем другой запрос
            search_query = get_related_search(original_song.title)
            player = await YTDLSource.from_url(f"ytsearch:{search_query}", loop=bot.loop, stream=True)
        
        return player
    except Exception as e:
        print(f"Ошибка при получении рекомендаций: {e}")
        return None

def check_queue(ctx, guild_id):
    """Проверка очереди и автоматическое добавление рекомендаций"""
    if guild_id in queues and queues[guild_id]:
        # Если есть песни в очереди, играем следующую
        next_song = queues[guild_id].pop(0)
        asyncio.run_coroutine_threadsafe(play_next(ctx, next_song), bot.loop)
    elif guild_id in radio_mode and radio_mode[guild_id] and guild_id in current_song:
        # Если включен радио-режим и есть текущий трек, добавляем рекомендацию
        asyncio.run_coroutine_threadsafe(add_recommendation(ctx, guild_id), bot.loop)

async def add_recommendation(ctx, guild_id):
    """Добавляет рекомендацию в очередь"""
    try:
        if guild_id in current_song and current_song[guild_id]:
            original = current_song[guild_id]
            
            # Отправляем уведомление о поиске рекомендаций
            notification = await ctx.send("🔍 Ищу похожую музыку...")
            
            # Получаем рекомендацию
            recommended = await get_recommendations(ctx, original)
            
            if recommended:
                # Добавляем в очередь
                if guild_id not in queues:
                    queues[guild_id] = []
                
                queues[guild_id].append(recommended)
                await notification.edit(content=f"✅ Добавлена рекомендация: **{recommended.title}**")
                
                # Если ничего не играет, начинаем воспроизведение
                if not ctx.voice_client.is_playing():
                    next_song = queues[guild_id].pop(0)
                    ctx.voice_client.play(next_song, after=lambda e: check_queue(ctx, guild_id))
                    await ctx.send(f"🎵 Сейчас играет: **{next_song.title}**")
            else:
                await notification.edit(content="❌ Не удалось найти похожую музыку")
    except Exception as e:
        print(f"Ошибка в add_recommendation: {e}")

async def play_next(ctx, song):
    """Воспроизводит следующую песню"""
    try:
        guild_id = ctx.guild.id
        current_song[guild_id] = song
        
        ctx.voice_client.play(song, after=lambda e: check_queue(ctx, guild_id))
        
        # Сохраняем в историю
        if guild_id not in history:
            history[guild_id] = []
        history[guild_id].append(song.title)
        # Ограничиваем историю 20 треками
        if len(history[guild_id]) > 20:
            history[guild_id].pop(0)
        
        # Отправляем сообщение с информацией о треке
        embed = discord.Embed(
            title="🎵 Сейчас играет",
            description=f"**{song.title}**",
            color=discord.Color.green()
        )
        if song.uploader:
            embed.add_field(name="Исполнитель", value=song.uploader, inline=True)
        if song.duration:
            minutes = song.duration // 60
            seconds = song.duration % 60
            embed.add_field(name="Длительность", value=f"{minutes}:{seconds:02d}", inline=True)
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Ошибка при воспроизведении: {str(e)}")
        check_queue(ctx, guild_id)

@bot.command(name='join', help='Подключиться к голосовому каналу')
async def join(ctx):
    if not ctx.message.author.voice:
        await ctx.send("❌ Вы не находитесь в голосовом канале!")
        return
    
    channel = ctx.message.author.voice.channel
    await channel.connect()
    await ctx.send(f"✅ Подключился к каналу: **{channel.name}**")

@bot.command(name='leave', help='Отключиться от голосового канала')
async def leave(ctx):
    guild_id = ctx.guild.id
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        # Очищаем данные сервера
        if guild_id in queues:
            queues[guild_id].clear()
        if guild_id in current_song:
            del current_song[guild_id]
        if guild_id in radio_mode:
            del radio_mode[guild_id]
        await ctx.send("👋 Отключился от голосового канала")
    else:
        await ctx.send("❌ Бот не находится в голосовом канале!")

@bot.command(name='play', help='Воспроизвести музыку с YouTube (по названию или ссылке)')
async def play(ctx, *, query):
    guild_id = ctx.guild.id
    
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("❌ Вы не находитесь в голосовом канале!")
            return

    # Отправляем уведомление о поиске
    async with ctx.typing():
        try:
            # Проверяем, является ли запрос ссылкой
            if not query.startswith('http'):
                search_query = f"ytsearch:{query}"
            else:
                search_query = query

            player = await YTDLSource.from_url(search_query, loop=bot.loop, stream=True)
            
            if ctx.voice_client.is_playing():
                # Добавляем в очередь
                if guild_id not in queues:
                    queues[guild_id] = []
                
                queues[guild_id].append(player)
                await ctx.send(f"✅ Добавлено в очередь: **{player.title}**")
            else:
                # Воспроизводим сразу
                current_song[guild_id] = player
                ctx.voice_client.play(player, after=lambda e: check_queue(ctx, guild_id))
                
                # Отправляем embed с информацией
                embed = discord.Embed(
                    title="🎵 Сейчас играет",
                    description=f"**{player.title}**",
                    color=discord.Color.green()
                )
                if player.uploader:
                    embed.add_field(name="Исполнитель", value=player.uploader, inline=True)
                if player.duration:
                    minutes = player.duration // 60
                    seconds = player.duration % 60
                    embed.add_field(name="Длительность", value=f"{minutes}:{seconds:02d}", inline=True)
                
                await ctx.send(embed=embed)
                
        except Exception as e:
            await ctx.send(f"❌ Ошибка: {str(e)}")

@bot.command(name='radio', help='Включить/выключить режим радио (автоподбор похожих песен)')
async def radio(ctx, mode: str = None):
    guild_id = ctx.guild.id
    
    if mode is None:
        # Показываем текущий статус
        status = "включен" if radio_mode.get(guild_id, False) else "выключен"
        await ctx.send(f"📻 Режим радио сейчас **{status}**")
        return
    
    if mode.lower() in ['on', 'вкл', '1', 'да']:
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            await ctx.send("❌ Сначала включи музыку командой `!play`")
            return
            
        radio_mode[guild_id] = True
        await ctx.send("📻 Режим радио **включен**! Бот будет автоматически добавлять похожие песни")
        
        # Если очередь пуста, сразу добавляем рекомендацию
        if guild_id not in queues or not queues[guild_id]:
            await add_recommendation(ctx, guild_id)
            
    elif mode.lower() in ['off', 'выкл', '0', 'нет']:
        radio_mode[guild_id] = False
        await ctx.send("📻 Режим радио **выключен**")
    else:
        await ctx.send("❌ Используй: `!radio on` или `!radio off`")

@bot.command(name='pause', help='Поставить музыку на паузу')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸ Музыка на паузе")
    else:
        await ctx.send("❌ Сейчас ничего не играет!")

@bot.command(name='resume', help='Продолжить воспроизведение')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶ Продолжаю воспроизведение")
    else:
        await ctx.send("❌ Музыка не на паузе!")

@bot.command(name='skip', help='Пропустить текущий трек')
async def skip(ctx):
    guild_id = ctx.guild.id
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭ Трек пропущен")
        
        # Если включен радио-режим и очередь пуста, добавляем рекомендацию
        if radio_mode.get(guild_id, False) and (guild_id not in queues or not queues[guild_id]):
            await add_recommendation(ctx, guild_id)
    else:
        await ctx.send("❌ Сейчас ничего не играет!")

@bot.command(name='stop', help='Остановить воспроизведение и очистить очередь')
async def stop(ctx):
    guild_id = ctx.guild.id
    if ctx.voice_client:
        ctx.voice_client.stop()
        if guild_id in queues:
            queues[guild_id].clear()
        if guild_id in current_song:
            del current_song[guild_id]
        await ctx.send("⏹ Воспроизведение остановлено, очередь очищена")
    else:
        await ctx.send("❌ Бот не в голосовом канале!")

@bot.command(name='queue', help='Показать текущую очередь')
async def show_queue(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues and queues[guild_id]:
        queue_list = []
        for i, song in enumerate(queues[guild_id][:10]):
            # Обрезаем длинные названия
            title = song.title if len(song.title) <= 50 else song.title[:47] + "..."
            queue_list.append(f"{i+1}. {title}")
        
        queue_text = "\n".join(queue_list)
        
        embed = discord.Embed(
            title="📋 Очередь воспроизведения",
            description=queue_text,
            color=discord.Color.blue()
        )
        
        if len(queues[guild_id]) > 10:
            embed.set_footer(text=f"И еще {len(queues[guild_id]) - 10} треков")
        
        await ctx.send(embed=embed)
    else:
        await ctx.send("📪 Очередь пуста")

@bot.command(name='history', help='Показать историю проигранных треков')
async def show_history(ctx):
    guild_id = ctx.guild.id
    if guild_id in history and history[guild_id]:
        history_list = []
        for i, title in enumerate(reversed(history[guild_id][-10:])):
            history_list.append(f"{i+1}. {title}")
        
        embed = discord.Embed(
            title="📜 История проигранных треков",
            description="\n".join(history_list),
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("📪 История пуста")

@bot.command(name='volume', help='Изменить громкость (0-100)')
async def volume(ctx, volume: int):
    if ctx.voice_client and ctx.voice_client.source:
        if 0 <= volume <= 100:
            ctx.voice_client.source.volume = volume / 100
            await ctx.send(f"🔊 Громкость установлена на {volume}%")
        else:
            await ctx.send("❌ Громкость должна быть от 0 до 100")
    else:
        await ctx.send("❌ Сейчас ничего не играет!")

@bot.command(name='now', help='Показать текущий трек')
async def now_playing(ctx):
    guild_id = ctx.guild.id
    if ctx.voice_client and ctx.voice_client.is_playing() and guild_id in current_song:
        song = current_song[guild_id]
        
        embed = discord.Embed(
            title="🎵 Сейчас играет",
            description=f"**{song.title}**",
            color=discord.Color.green()
        )
        if song.uploader:
            embed.add_field(name="Исполнитель", value=song.uploader, inline=True)
        if song.duration:
            minutes = song.duration // 60
            seconds = song.duration % 60
            embed.add_field(name="Длительность", value=f"{minutes}:{seconds:02d}", inline=True)
        
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Сейчас ничего не играет!")

# Запуск бота
if __name__ == "__main__":
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        print("❌ Ошибка: Не найден DISCORD_TOKEN в переменных окружения!")
        print("Создайте файл .env и добавьте строку: DISCORD_TOKEN=ваш_токен_бота")
    else:
        bot.run(token)