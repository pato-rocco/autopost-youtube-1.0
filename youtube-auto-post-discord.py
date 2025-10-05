import os
import glob
import asyncio
import json
import requests
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Para Discord
import discord
from discord.ext import commands
from discord.ui import Select, View, Button

# Para YouTube API
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# Carregar variáveis do arquivo .env
load_dotenv()

# Configurações
PASTA_VIDEOS = "./videos"
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
CANAL_DISCORD_ID = 1422768699923763382  # ID do canal específico
TIMEOUT_INTERACOES = 300  # 5 minutos em segundos

# Carregar variáveis de ambiente
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

if not DISCORD_BOT_TOKEN:
    print("❌ DISCORD_BOT_TOKEN não encontrado no arquivo .env")
    print("💡 Verifique se o arquivo .env existe e contém DISCORD_BOT_TOKEN")
    exit(1)

if not DEEPSEEK_API_KEY:
    print("⚠️ DEEPSEEK_API_KEY não encontrado no arquivo .env")

# Configuração do bot Discord
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ========== FUNÇÃO VERIFICADORA DO CANAL ==========
def verificar_canal_correto():
    """Decorator para verificar se o comando foi executado no canal correto"""
    async def predicate(ctx):
        if ctx.channel.id != CANAL_DISCORD_ID:
            await ctx.send(f"❌ Este comando só pode ser usado no canal designado. Canal atual: {ctx.channel.id}")
            return False
        return True
    return commands.check(predicate)

# Dicionário para armazenar seleções em andamento
selecoes_ativas = {}
# Dicionário para armazenar status de upload em andamento
uploads_ativos = {}
# Dicionário para armazenar metadados em revisão
revisoes_ativas = {}

# ========== SISTEMA DE FILA DE UPLOADS ==========
fila_uploads = asyncio.Queue()
upload_em_andamento = False
fila_ativa = {}
mensagem_fila_global = None  # Mensagem global da fila
ultima_mensagem_status = None  # Última mensagem de status do upload

# ========== VIEW BASE COM BOTÃO HOME ==========
class ViewComHome(View):
    """View base que inclui botão Home em todas as páginas"""
    def __init__(self, timeout=300):
        super().__init__(timeout=timeout)
    
    @discord.ui.button(label="🏠 Home", style=discord.ButtonStyle.primary, emoji="🏠", row=4)
    async def home_button(self, interaction: discord.Interaction, button: Button):
        """Botão Home para voltar ao menu principal"""
        try:
            await interaction.response.defer()
            await mostrar_menu_principal(interaction=interaction)
        except Exception as e:
            print(f"Erro no botão Home: {e}")
            try:
                await interaction.followup.send("❌ Erro ao voltar ao menu principal.", ephemeral=True)
            except:
                channel = interaction.channel
                await channel.send("❌ Erro ao voltar ao menu principal.")

class TarefaUpload:
    def __init__(self, ctx, video_info, titulo, descricao, thumbnail_path=None, agendar=None):
        self.ctx = ctx
        self.video_info = video_info
        self.titulo = titulo
        self.descricao = descricao
        self.thumbnail_path = thumbnail_path
        self.agendar = agendar
        self.id_tarefa = f"{ctx.author.id}_{datetime.now().timestamp()}"
        self.status = "na_fila"
        self.mensagem_status = None
        self.posicao = 0

class AgendamentoSelect(Select):
    def __init__(self, opcoes_agendamento):
        super().__init__(
            placeholder="🎯 Selecione uma data para agendamento...",
            min_values=1,
            max_values=1,
            options=opcoes_agendamento
        )
        self.opcoes_agendamento = opcoes_agendamento
    
    async def callback(self, interaction: discord.Interaction):
        # Encontrar a opção selecionada
        opcao_selecionada = None
        for opcao in self.opcoes_agendamento:
            if opcao.value == self.values[0]:
                opcao_selecionada = opcao
                break
        
        if opcao_selecionada:
            # Extrair a data do value
            data_selecionada = opcao_selecionada.value
            await interaction.response.send_message(
                f"⏰ **Data selecionada:** {opcao_selecionada.label}\n`{data_selecionada}`", 
                ephemeral=True
            )
            
            # Armazenar a seleção no contexto
            self.view.agendamento_selecionado = data_selecionada
            self.view.stop()

class AgendamentoView(ViewComHome):
    def __init__(self, opcoes_agendamento, timeout=300):
        super().__init__(timeout=timeout)
        self.agendamento_selecionado = None
        self.add_item(AgendamentoSelect(opcoes_agendamento))

class ValidacaoView(ViewComHome):
    def __init__(self, timeout=300):
        super().__init__(timeout=timeout)
        self.aprovado = False
        self.editar_titulo = False
        self.editar_descricao = False
    
    @discord.ui.button(label="✅ Aprovar Tudo", style=discord.ButtonStyle.success, emoji="✅")
    async def aprovar_tudo(self, interaction: discord.Interaction, button: Button):
        self.aprovado = True
        await interaction.response.send_message("✅ **Metadados aprovados!** Continuando com o upload...", ephemeral=True)
        self.stop()
    
    @discord.ui.button(label="✏️ Editar Título", style=discord.ButtonStyle.primary, emoji="✏️")
    async def editar_titulo_btn(self, interaction: discord.Interaction, button: Button):
        self.editar_titulo = True
        await interaction.response.send_message("📝 **Envie o novo título** no chat (você tem 5 minutos):", ephemeral=True)
        self.stop()
    
    @discord.ui.button(label="📝 Editar Descrição", style=discord.ButtonStyle.primary, emoji="📝")
    async def editar_descricao_btn(self, interaction: discord.Interaction, button: Button):
        self.editar_descricao = True
        await interaction.response.send_message("📄 **Envie a nova descrição** no chat (você tem 5 minutos):", ephemeral=True)
        self.stop()
    
    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancelar(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("❌ **Upload cancelado.**", ephemeral=True)
        self.stop()

# ========== SISTEMA DE FLUXO CONTÍNUO CORRIGIDO ==========

class FluxoContinuoView(ViewComHome):
    def __init__(self, timeout=300):
        super().__init__(timeout=timeout)
    
    @discord.ui.button(label="🎬 Adicionar Novo Vídeo", style=discord.ButtonStyle.success, emoji="🎬")
    async def adicionar_novo(self, interaction: discord.Interaction, button: Button):
        """CORREÇÃO: Resposta imediata e depois processamento"""
        try:
            # Responder imediatamente à interação
            await interaction.response.send_message("🔄 Iniciando processo para novo vídeo...", ephemeral=True)
            
            # Usar followup para evitar problemas de timeout
            await interaction.followup.send("📋 Listando vídeos disponíveis...", ephemeral=False)
            
            # Chamar a função de listar vídeos
            await listar_videos_reacao(interaction=interaction)
            
        except Exception as e:
            print(f"Erro no botão Adicionar Novo Vídeo: {e}")
            try:
                await interaction.followup.send("❌ Erro ao processar solicitação. Tente novamente.", ephemeral=True)
            except:
                # Se tudo falhar, enviar mensagem normal
                channel = interaction.channel
                await channel.send("❌ Erro ao processar solicitação. Tente novamente.")
    
    @discord.ui.button(label="📊 Ver Fila Completa", style=discord.ButtonStyle.primary, emoji="📊")
    async def ver_fila(self, interaction: discord.Interaction, button: Button):
        """CORREÇÃO: Resposta imediata e depois processamento"""
        try:
            await interaction.response.send_message("📊 Buscando status da fila...", ephemeral=True)
            await mostrar_fila_detalhada(interaction=interaction)
        except Exception as e:
            print(f"Erro no botão Ver Fila: {e}")
            try:
                await interaction.followup.send("❌ Erro ao buscar fila.", ephemeral=True)
            except:
                channel = interaction.channel
                await channel.send("❌ Erro ao buscar fila.")
    
    @discord.ui.button(label="⚙️ Status do Sistema", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def status_sistema(self, interaction: discord.Interaction, button: Button):
        """CORREÇÃO: Resposta imediata e depois processamento"""
        try:
            await interaction.response.send_message("⚙️ Buscando status do sistema...", ephemeral=True)
            await mostrar_status_sistema(interaction=interaction)
        except Exception as e:
            print(f"Erro no botão Status Sistema: {e}")
            try:
                await interaction.followup.send("❌ Erro ao buscar status.", ephemeral=True)
            except:
                channel = interaction.channel
                await channel.send("❌ Erro ao buscar status.")
    
    @discord.ui.button(label="❌ Finalizar", style=discord.ButtonStyle.danger, emoji="❌")
    async def finalizar(self, interaction: discord.Interaction, button: Button):
        """CORREÇÃO: Resposta simples e direta"""
        try:
            await interaction.response.send_message("✅ **Processo finalizado.** Você pode reiniciar a qualquer momento usando `!listar` ou o menu principal.", ephemeral=True)
        except Exception as e:
            print(f"Erro no botão Finalizar: {e}")
            channel = interaction.channel
            await channel.send("✅ **Processo finalizado.**")

# ========== SISTEMA DE MENU POR REAÇÃO CORRIGIDO ==========

class MenuView(ViewComHome):
    def __init__(self, timeout=300):
        super().__init__(timeout=timeout)
    
    @discord.ui.button(label="📋 Comandos", style=discord.ButtonStyle.primary, emoji="📋")
    async def comandos(self, interaction: discord.Interaction, button: Button):
        """CORREÇÃO: Resposta imediata e depois processamento"""
        try:
            await interaction.response.send_message("📋 Carregando comandos...", ephemeral=True)
            await mostrar_comandos(interaction=interaction)
        except Exception as e:
            print(f"Erro no botão Comandos: {e}")
            try:
                await interaction.followup.send("❌ Erro ao carregar comandos.", ephemeral=True)
            except:
                channel = interaction.channel
                await channel.send("❌ Erro ao carregar comandos.")
    
    @discord.ui.button(label="🎬 Listar Vídeos", style=discord.ButtonStyle.success, emoji="🎬")
    async def listar_videos(self, interaction: discord.Interaction, button: Button):
        """CORREÇÃO: Resposta imediata e depois processamento"""
        try:
            await interaction.response.send_message("🔄 Iniciando processo de listagem...", ephemeral=True)
            await listar_videos_reacao(interaction=interaction)
        except Exception as e:
            print(f"Erro no botão Listar Vídeos: {e}")
            try:
                await interaction.followup.send("❌ Erro ao listar vídeos.", ephemeral=True)
            except:
                channel = interaction.channel
                await channel.send("❌ Erro ao listar vídeos.")
    
    @discord.ui.button(label="📊 Status da Fila", style=discord.ButtonStyle.secondary, emoji="📊")
    async def status_fila(self, interaction: discord.Interaction, button: Button):
        """CORREÇÃO: Resposta imediata e depois processamento"""
        try:
            await interaction.response.send_message("📊 Buscando status da fila...", ephemeral=True)
            await mostrar_fila_detalhada(interaction=interaction)
        except Exception as e:
            print(f"Erro no botão Status Fila: {e}")
            try:
                await interaction.followup.send("❌ Erro ao buscar fila.", ephemeral=True)
            except:
                channel = interaction.channel
                await channel.send("❌ Erro ao buscar fila.")
    
    @discord.ui.button(label="⚙️ Status Sistema", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def status_sistema(self, interaction: discord.Interaction, button: Button):
        """CORREÇÃO: Resposta imediata e depois processamento"""
        try:
            await interaction.response.send_message("⚙️ Buscando status do sistema...", ephemeral=True)
            await mostrar_status_sistema(interaction=interaction)
        except Exception as e:
            print(f"Erro no botão Status Sistema: {e}")
            try:
                await interaction.followup.send("❌ Erro ao buscar status.", ephemeral=True)
            except:
                channel = interaction.channel
                await channel.send("❌ Erro ao buscar status.")

# ========== FUNÇÕES DO SISTEMA DE FILA ==========

async def gerenciador_fila_uploads():
    """Processa a fila de uploads sequencialmente"""
    global upload_em_andamento, ultima_mensagem_status
    
    while True:
        try:
            if not fila_uploads.empty() and not upload_em_andamento:
                upload_em_andamento = True
                tarefa = await fila_uploads.get()
                
                # Atualizar status
                tarefa.status = "em_upload"
                await atualizar_status_fila(tarefa)
                await atualizar_fila_global()
                
                # Executar upload
                try:
                    resultado = await upload_youtube_real(
                        tarefa.ctx,
                        tarefa.mensagem_status,
                        tarefa.video_info['video'],
                        tarefa.titulo,
                        tarefa.descricao,
                        tarefa.thumbnail_path,
                        tarefa.agendar if tarefa.agendar != "imediato" else None
                    )
                    
                    # Notificar conclusão
                    await notificar_conclusao_upload(tarefa, resultado)
                    
                    # OFERECER PRÓXIMO PASSO APÓS CONCLUSÃO
                    if resultado['status'] == 'sucesso':
                        await asyncio.sleep(2)  # Pequena pausa para melhor UX
                        await oferecer_proximo_passo(tarefa.ctx, tarefa.titulo)
                    
                except Exception as e:
                    print(f"Erro durante o upload: {e}")
                    await tarefa.ctx.send(f"❌ Erro durante o upload: {str(e)}")
                
                finally:
                    # Limpar e processar próximo
                    upload_em_andamento = False
                    fila_uploads.task_done()
                    
                    # Remover da fila ativa
                    if tarefa.id_tarefa in fila_ativa:
                        del fila_ativa[tarefa.id_tarefa]
                    
                    # Atualizar fila global
                    await atualizar_fila_global()
            
            await asyncio.sleep(5)  # Verificar a cada 5 segundos
            
        except Exception as e:
            print(f"Erro no gerenciador de fila: {e}")
            upload_em_andamento = False
            await asyncio.sleep(10)

async def adicionar_na_fila(ctx, video_info, titulo, descricao, thumbnail_path=None, agendar=None):
    """Adiciona um vídeo à fila de uploads"""
    global ultima_mensagem_status
    
    tarefa = TarefaUpload(ctx, video_info, titulo, descricao, thumbnail_path, agendar)
    
    # Calcular posição na fila
    posicao = fila_uploads.qsize() + 1
    tarefa.posicao = posicao
    
    # Criar mensagem de status na fila
    embed_fila = discord.Embed(
        title="📋 Vídeo Adicionado à Fila de Upload",
        color=0xffff00
    )
    embed_fila.add_field(name="🎬 Título", value=f"```{titulo[:100]}...```" if len(titulo) > 100 else f"```{titulo}```", inline=False)
    embed_fila.add_field(name="📊 Status", value="⏳ **Na Fila** - Aguardando vez...", inline=False)
    embed_fila.add_field(name="📊 Posição na Fila", value=f"`{posicao}`", inline=True)
    
    if agendar and agendar != "imediato":
        embed_fila.add_field(name="⏰ Agendamento", value=f"`{agendar}`", inline=True)
    
    mensagem_fila = await ctx.send(embed=embed_fila)
    tarefa.mensagem_status = mensagem_fila
    
    # Atualizar última mensagem de status
    ultima_mensagem_status = mensagem_fila
    
    # Adicionar à fila e dicionário ativo
    await fila_uploads.put(tarefa)
    fila_ativa[tarefa.id_tarefa] = tarefa
    
    # Iniciar gerenciador se não estiver rodando
    if not hasattr(bot, 'gerenciador_fila_iniciado'):
        bot.gerenciador_fila_iniciado = True
        bot.loop.create_task(gerenciador_fila_uploads())
    
    # ATUALIZAR FILA GLOBAL SEMPRE QUE ADICIONAR NOVO VÍDEO
    await atualizar_fila_global()
    
    return tarefa

async def atualizar_status_fila(tarefa):
    """Atualiza o status de um item na fila"""
    global ultima_mensagem_status
    
    try:
        # Verificar se a mensagem ainda existe e é acessível
        try:
            await tarefa.mensagem_status.channel.fetch_message(tarefa.mensagem_status.id)
        except discord.NotFound:
            print("Mensagem de status não encontrada, criando nova...")
            # Recriar a mensagem de status
            embed = discord.Embed(
                title="📋 Status do Vídeo na Fila",
                color=0xffff00
            )
            embed.add_field(name="🎬 Título", value=f"```{tarefa.titulo[:100]}...```" if len(tarefa.titulo) > 100 else f"```{tarefa.titulo}```", inline=False)
            
            if tarefa.status == "na_fila":
                status_text = f"⏳ **Na Fila** - Posição: `{tarefa.posicao}`"
            elif tarefa.status == "em_upload":
                status_text = "📤 **Fazendo Upload** - Processando..."
            elif tarefa.status == "concluido":
                status_text = "✅ **Concluído**"
            elif tarefa.status == "erro":
                status_text = "❌ **Erro no Upload**"
            
            embed.add_field(name="📊 Status", value=status_text, inline=False)
            embed.add_field(name="📊 Posição na Fila", value=f"`{tarefa.posicao}`", inline=True)
            
            if tarefa.agendar and tarefa.agendar != "imediato":
                embed.add_field(name="⏰ Agendamento", value=f"`{tarefa.agendar}`", inline=True)
            
            nova_mensagem = await tarefa.ctx.send(embed=embed)
            tarefa.mensagem_status = nova_mensagem
            return

        embed = tarefa.mensagem_status.embeds[0]
        
        # Atualizar campos
        embed.clear_fields()
        embed.add_field(name="🎬 Título", value=f"```{tarefa.titulo[:100]}...```" if len(tarefa.titulo) > 100 else f"```{tarefa.titulo}```", inline=False)
        
        if tarefa.status == "na_fila":
            status_text = f"⏳ **Na Fila** - Posição: `{tarefa.posicao}`"
        elif tarefa.status == "em_upload":
            status_text = "📤 **Fazendo Upload** - Processando..."
        elif tarefa.status == "concluido":
            status_text = "✅ **Concluído**"
        elif tarefa.status == "erro":
            status_text = "❌ **Erro no Upload**"
        
        embed.add_field(name="📊 Status", value=status_text, inline=False)
        embed.add_field(name="📊 Posição na Fila", value=f"`{tarefa.posicao}`", inline=True)
        
        if tarefa.agendar and tarefa.agendar != "imediato":
            embed.add_field(name="⏰ Agendamento", value=f"`{tarefa.agendar}`", inline=True)
        
        await tarefa.mensagem_status.edit(embed=embed)
        
        # Atualizar última mensagem de status
        ultima_mensagem_status = tarefa.mensagem_status
        
    except Exception as e:
        print(f"Erro ao atualizar status da fila: {e}")

async def atualizar_fila_global():
    """Atualiza a mensagem global da fila"""
    global mensagem_fila_global
    
    try:
        # Buscar todas as tarefas ativas
        tarefas_ativas = list(fila_ativa.values())
        tarefas_ativas.sort(key=lambda x: x.posicao)
        
        embed = discord.Embed(
            title="🔄 Fila de Uploads - Visão Geral",
            color=0x0099ff,
            timestamp=datetime.now()
        )
        
        if not tarefas_ativas and not upload_em_andamento:
            embed.description = "📭 **Fila vazia** - Nenhum upload pendente"
        else:
            # Upload atual
            if upload_em_andamento:
                embed.add_field(
                    name="🎬 Upload Atual", 
                    value="📤 **Processando upload em andamento...**", 
                    inline=False
                )
            
            # Itens na fila
            if tarefas_ativas:
                embed.add_field(
                    name=f"⏳ Uploads Pendentes ({len(tarefas_ativas)})", 
                    value="\n".join([f"`{t.posicao}.` {t.titulo[:50]}... - {t.status}" for t in tarefas_ativas]),
                    inline=False
                )
            
            embed.add_field(
                name="📊 Estatísticas",
                value=f"• Uploads na fila: `{len(tarefas_ativas)}`\n• Upload em andamento: `{'Sim' if upload_em_andamento else 'Não'}`\n• Próxima posição: `{len(tarefas_ativas) + 1}`",
                inline=False
            )
        
        embed.set_footer(text="Fila atualizada automaticamente")
        
        # Atualizar ou criar mensagem global
        if mensagem_fila_global:
            try:
                await mensagem_fila_global.edit(embed=embed)
            except discord.NotFound:
                # Se a mensagem foi deletada, criar nova
                canal = bot.get_channel(CANAL_DISCORD_ID)
                if canal:
                    mensagem_fila_global = await canal.send(embed=embed)
            except Exception as e:
                print(f"Erro ao editar mensagem global da fila: {e}")
        else:
            canal = bot.get_channel(CANAL_DISCORD_ID)
            if canal:
                mensagem_fila_global = await canal.send(embed=embed)
                
    except Exception as e:
        print(f"Erro ao atualizar fila global: {e}")

# ========== FUNÇÃO PARA EXCLUIR ARQUIVOS DO VÍDEO ==========

async def excluir_arquivos_video(video_info, ctx):
    """Exclui todos os arquivos relacionados ao vídeo após upload bem-sucedido"""
    try:
        arquivos_excluidos = []
        arquivos_erros = []
        
        # Lista de tipos de arquivos para excluir
        tipos_arquivos = ['video', 'contexto', 'thumb', 'legendas']
        
        for tipo in tipos_arquivos:
            if tipo in video_info and video_info[tipo]:
                caminho_arquivo = video_info[tipo]
                try:
                    if os.path.exists(caminho_arquivo):
                        os.remove(caminho_arquivo)
                        arquivos_excluidos.append(f"`{os.path.basename(caminho_arquivo)}`")
                        print(f"✅ Arquivo excluído: {caminho_arquivo}")
                    else:
                        arquivos_erros.append(f"`{os.path.basename(caminho_arquivo)}` (não encontrado)")
                except Exception as e:
                    arquivos_erros.append(f"`{os.path.basename(caminho_arquivo)}` (erro: {str(e)})")
                    print(f"❌ Erro ao excluir {caminho_arquivo}: {e}")
        
        # Verificar se há uma pasta com o nome base do vídeo e tentar excluí-la se estiver vazia
        nome_base = video_info.get('nome_base', '')
        if nome_base:
            pasta_video = os.path.join(PASTA_VIDEOS, nome_base)
            if os.path.exists(pasta_video) and os.path.isdir(pasta_video):
                try:
                    # Verificar se a pasta está vazia
                    if not os.listdir(pasta_video):
                        os.rmdir(pasta_video)
                        arquivos_excluidos.append(f"`pasta {nome_base}/`")
                        print(f"✅ Pasta vazia excluída: {pasta_video}")
                    else:
                        print(f"ℹ️ Pasta não vazia, mantida: {pasta_video}")
                except Exception as e:
                    print(f"❌ Erro ao excluir pasta {pasta_video}: {e}")
        
        # Criar embed de relatório de exclusão
        embed_exclusao = discord.Embed(
            title="🗑️ Limpeza de Arquivos Concluída",
            color=0x00ff00,
            timestamp=datetime.now()
        )
        
        if arquivos_excluidos:
            embed_exclusao.add_field(
                name="✅ Arquivos Excluídos",
                value="\n".join(arquivos_excluidos),
                inline=False
            )
        
        if arquivos_erros:
            embed_exclusao.add_field(
                name="⚠️ Arquivos com Problemas",
                value="\n".join(arquivos_erros),
                inline=False
            )
        
        if not arquivos_excluidos and not arquivos_erros:
            embed_exclusao.description = "ℹ️ Nenhum arquivo encontrado para exclusão."
        
        embed_exclusao.set_footer(text="Arquivos limpos automaticamente após upload bem-sucedido")
        
        # CORREÇÃO: Usar uma nova mensagem em vez de tentar editar mensagens antigas
        await ctx.send(embed=embed_exclusao)
        
        return len(arquivos_excluidos), len(arquivos_erros)
        
    except Exception as e:
        print(f"❌ Erro geral na exclusão de arquivos: {e}")
        # CORREÇÃO: Usar uma nova mensagem em vez de tentar editar mensagens antigas
        await ctx.send(f"❌ **Erro na limpeza de arquivos:** {str(e)}")
        return 0, 1

async def notificar_conclusao_upload(tarefa, resultado):
    """Notifica a conclusão do upload e exclui os arquivos se bem-sucedido"""
    global ultima_mensagem_status
    
    if resultado['status'] == 'sucesso':
        embed_final = discord.Embed(
            title="🎉 Upload Concluído com Sucesso!",
            color=0x00ff00
        )
        embed_final.add_field(name="🎬 Título", value=tarefa.titulo, inline=False)
        embed_final.add_field(name="🔗 URL do Vídeo", value=resultado['url'], inline=False)
        
        if tarefa.agendar and tarefa.agendar != "imediato":
            embed_final.add_field(name="⏰ Agendado para", value=tarefa.agendar, inline=False)
        
        embed_final.add_field(name="📊 Status", value="✅ Vídeo publicado com sucesso!", inline=False)
        
        # CORREÇÃO: Sempre criar nova mensagem em vez de editar mensagens antigas
        mensagem_conclusao = await tarefa.ctx.send(embed=embed_final)
        ultima_mensagem_status = mensagem_conclusao
        
        # EXCLUIR ARQUIVOS APÓS UPLOAD BEM-SUCEDIDO
        await asyncio.sleep(2)  # Pequena pausa antes da limpeza
        await tarefa.ctx.send("🗑️ **Iniciando limpeza automática de arquivos...**")
        
        arquivos_excluidos, arquivos_erros = await excluir_arquivos_video(tarefa.video_info, tarefa.ctx)
        
        if arquivos_excluidos > 0:
            await tarefa.ctx.send(f"✅ **Limpeza concluída!** `{arquivos_excluidos}` arquivo(s) excluído(s).")
        else:
            await tarefa.ctx.send("ℹ️ **Nenhum arquivo foi excluído.** Verifique se os arquivos ainda existem.")
        
    else:
        embed_erro = discord.Embed(
            title="❌ Falha no Upload",
            color=0xff0000
        )
        embed_erro.add_field(name="📄 Detalhes do Erro", value=resultado['mensagem'], inline=False)
        # CORREÇÃO: Sempre criar nova mensagem em vez de editar mensagens antigas
        mensagem_erro = await tarefa.ctx.send(embed=embed_erro)
        ultima_mensagem_status = mensagem_erro

async def oferecer_proximo_passo(ctx, ultimo_video_titulo=None):
    """Oferece o próximo passo após um upload ser adicionado à fila ou concluído"""
    global ultima_mensagem_status
    
    embed = discord.Embed(
        title="🎯 Próximo Passo - O que deseja fazer?",
        color=0x00ff00,
        timestamp=datetime.now()
    )
    
    if ultimo_video_titulo:
        embed.add_field(
            name="✅ Upload Concluído",
            value=f"**`{ultimo_video_titulo}`**\n*foi processado com sucesso!*",
            inline=False
        )
    
    embed.add_field(
        name="🔄 Opções Disponíveis",
        value=(
            "**🎬 Adicionar Novo Vídeo** - Iniciar processo para outro vídeo\n"
            "**📊 Ver Fila Completa** - Status atual de todos os uploads\n"
            "**⚙️ Status do Sistema** - Ver configurações e estatísticas\n"
            "**❌ Finalizar** - Encerrar sessão atual"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📈 Estatísticas da Fila",
        value=f"• Uploads na fila: `{fila_uploads.qsize()}`\n• Upload em andamento: `{'Sim' if upload_em_andamento else 'Não'}`",
        inline=False
    )
    
    embed.add_field(
        name="🗑️ Limpeza Automática",
        value="✅ **Arquivos excluídos** - Todos os arquivos do vídeo foram removidos automaticamente após o upload",
        inline=False
    )
    
    embed.set_footer(text="Selecione uma opção abaixo para continuar")
    
    view = FluxoContinuoView(timeout=TIMEOUT_INTERACOES)
    mensagem = await ctx.send(embed=embed, view=view)
    
    # Atualizar última mensagem de status
    ultima_mensagem_status = mensagem
    
    return mensagem

# ========== FUNÇÃO PARA MOSTRAR MENU PRINCIPAL ==========

async def mostrar_menu_principal(interaction=None, ctx=None, channel=None):
    """Mostra o menu principal (Home) - VERSÃO CORRIGIDA"""
    global ultima_mensagem_status
    
    # Determinar o canal de destino
    if interaction:
        channel = interaction.channel
    elif ctx:
        channel = ctx.channel
    elif channel is None:
        print("❌ Erro: nenhum canal, interação ou contexto fornecido para mostrar_menu_principal")
        return
    
    embed = discord.Embed(
        title="🤖 Bot de Automação YouTube - Menu Principal",
        description="**Estou pronto para ajudar! Use os botões abaixo para navegar:**",
        color=0x00ff00,
        timestamp=datetime.now()
    )
    
    embed.add_field(
        name="📋 Menu de Navegação",
        value=(
            "**📋 Comandos** - Mostra lista de comandos\n"
            "**🎬 Listar Vídeos** - Lista vídeos para upload\n"
            "**📊 Status da Fila** - Mostra fila de uploads\n"
            "**⚙️ Status Sistema** - Mostra status do sistema"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🔄 Sistema de Fila com Fluxo Contínuo",
        value="**Novo:** Após cada upload, opções automáticas para próximo vídeo\n**Otimizado:** Processo contínuo e intuitivo",
        inline=False
    )
    
    embed.add_field(
        name="🎯 Como Usar",
        value=(
            "1. **Listar Vídeos** - Ver vídeos disponíveis\n"
            "2. **Selecionar por reação** - Escolher vídeos\n"
            "3. **Validar metadados** - Revisar título/descrição\n"
            "4. **Adicionar à fila** - Upload automático\n"
            "5. **Fluxo contínuo** - Sempre oferece próximo passo"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🔧 Funcionalidades Recentes",
        value=(
            "✅ **Verificação de arquivos obrigatórios**\n"
            "✅ **Botão Home em todas as páginas**\n"
            "✅ **Sistema de fila otimizado**\n"
            "✅ **Uploads em background**\n"
            "✅ **Limpeza automática de arquivos**"
        ),
        inline=False
    )
    
    embed.set_footer(text="Use os botões abaixo para navegar ou os comandos ! tradicionais")
    
    view = MenuView(timeout=TIMEOUT_INTERACOES)
    
    if interaction:
        try:
            # Tentar editar a mensagem original se possível
            if hasattr(interaction, 'response') and not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        except Exception as e:
            print(f"Erro ao mostrar menu principal via interação: {e}")
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)
    else:
        mensagem = await channel.send(embed=embed, view=view)
        ultima_mensagem_status = mensagem

async def mostrar_fila_detalhada(interaction=None, ctx=None):
    """Mostra a fila detalhada (para reação ou comando)"""
    global ultima_mensagem_status
    
    channel = interaction.channel if interaction else ctx.channel
    
    if fila_uploads.empty() and not upload_em_andamento:
        embed = discord.Embed(
            title="📊 Fila de Uploads",
            description="📭 **Fila vazia** - Nenhum upload pendente",
            color=0x00ff00
        )
        view = ViewComHome(timeout=TIMEOUT_INTERACOES)
        if interaction:
            mensagem = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        else:
            mensagem = await ctx.send(embed=embed, view=view)
        ultima_mensagem_status = mensagem
        return
    
    embed = discord.Embed(
        title="📊 Status Detalhado da Fila de Uploads",
        color=0x0099ff
    )
    
    # Upload atual
    if upload_em_andamento:
        embed.add_field(
            name="🎬 Upload Atual", 
            value="📤 **Processando upload em andamento...**", 
            inline=False
        )
    
    # Itens na fila
    if fila_uploads.qsize() > 0:
        embed.add_field(
            name=f"⏳ Uploads Pendentes", 
            value=f"`{fila_uploads.qsize()}` vídeos na fila", 
            inline=False
        )
    
    embed.add_field(
        name="📋 Ações Disponíveis",
        value=(
            "**🎬 Adicionar Novo Vídeo** - Iniciar processo para outro vídeo\n"
            "**📊 Status da Fila** - Ver status atualizado\n"
            "**⚙️ Status Sistema** - Ver configurações\n"
            "Uploads são processados automaticamente 🚀"
        ),
        inline=False
    )
    
    view = ViewComHome(timeout=TIMEOUT_INTERACOES)
    
    if interaction:
        mensagem = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
    else:
        mensagem = await ctx.send(embed=embed, view=view)
    
    ultima_mensagem_status = mensagem

# ========== FUNÇÕES DE MENU POR REAÇÃO CORRIGIDAS ==========

async def mostrar_comandos(interaction=None, ctx=None):
    """Mostra comandos disponíveis (para reação ou comando)"""
    global ultima_mensagem_status
    
    channel = interaction.channel if interaction else ctx.channel
    
    embed = discord.Embed(
        title="📋 Menu de Comandos - Bot YouTube",
        description="**Selecione uma opção abaixo ou use os botões:**",
        color=0x0099ff
    )
    
    embed.add_field(
        name="🎬 Comandos Principais",
        value=(
            "**📋 Comandos** - Mostra esta lista\n"
            "**🎬 Listar Vídeos** - Lista vídeos para upload\n"
            "**📊 Status da Fila** - Mostra fila de uploads\n"
            "**⚙️ Status Sistema** - Mostra status do sistema"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🔄 Sistema de Fila",
        value=(
            "**Preparação simultânea** - Enquanto um upload roda, prepare outros\n"
            "**Processamento automático** - Fila processa sequencialmente\n"
            "**Status em tempo real** - Veja posição e status de cada vídeo"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎯 Fluxo Contínuo",
        value=(
            "**Após cada upload** - Opções para próximo vídeo\n"
            "**Navegação intuitiva** - Sempre sabe o que fazer\n"
            "**Processo otimizado** - Máximo de eficiência"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Comandos de Texto",
        value=(
            "`!comandos` - Mostra esta lista\n"
            "`!listar` - Lista vídeos disponíveis\n"
            "`!fila` - Mostra status da fila\n"
            "`!status` - Mostra status do sistema\n"
            "`!home` - Volta ao menu principal\n"
            "`!auth_youtube` - Reautentica com YouTube (dono)\n"
            "`!limpar_fila` - Limpa a fila (dono)"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🗑️ Limpeza Automática",
        value="Após cada upload bem-sucedido, todos os arquivos do vídeo (vídeo, contexto, thumbnail, legendas) são **excluídos automaticamente** para liberar espaço.",
        inline=False
    )
    
    view = ViewComHome(timeout=TIMEOUT_INTERACOES)
    
    if interaction:
        mensagem = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
    else:
        mensagem = await ctx.send(embed=embed, view=view)
    
    ultima_mensagem_status = mensagem

async def listar_videos_reacao(interaction=None, ctx=None):
    """Lista vídeos por reação (para reação ou comando)"""
    global ultima_mensagem_status
    
    channel = interaction.channel if interaction else ctx.channel
    author = interaction.user if interaction else ctx.author
    
    arquivos = listar_arquivos_vinculados()
    
    if not arquivos:
        view = ViewComHome(timeout=TIMEOUT_INTERACOES)
        if interaction:
            mensagem = await interaction.followup.send("📭 Nenhum vídeo encontrado na pasta.", view=view, ephemeral=False)
        else:
            mensagem = await ctx.send("📭 Nenhum vídeo encontrado na pasta.", view=view)
        ultima_mensagem_status = mensagem
        return
    
    # Converter para lista para facilitar a indexação
    lista_arquivos = list(arquivos.items())
    
    embed = discord.Embed(
        title="📹 Selecione um Vídeo para Processar",
        description=f"Encontrados {len(arquivos)} vídeos. Reaja com o número correspondente:",
        color=0x00ff00
    )
    
    # Emojis numéricos
    emojis_numeros = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
    
    for i, (nome, info) in enumerate(lista_arquivos[:10]):  # Limitar a 10 vídeos
        # Extrair informações do arquivo para mostrar no preview
        nome_jogo, numero_episodio = extrair_info_arquivo(info.get('video', ''))
        
        valor = f"**Vídeo:** `{os.path.basename(info.get('video'))}`\n"
        valor += f"**Jogo Detectado:** {nome_jogo}\n"
        if numero_episodio:
            valor += f"**Episódio:** {numero_episodio}\n"
        
        # DESTACAR SE FALTAM ARQUIVOS OBRIGATÓRIOS
        if not info.get('contexto'):
            valor += "⚠️ **CONTEXTO: ❌ FALTANDO**\n"
        else:
            valor += "📄 Contexto: ✅\n"
            
        if not info.get('thumb'):
            valor += "⚠️ **THUMBNAIL: ❌ FALTANDO**\n"
        else:
            valor += "🖼️ Thumbnail: ✅\n"
            
        if info.get('legendas'):
            valor += "🎯 Legendas: ✅\n"
        
        embed.add_field(name=f"{emojis_numeros[i]} {nome}", value=valor, inline=False)
    
    if len(lista_arquivos) > 10:
        embed.add_field(
            name="⚠️ Limite Atingido", 
            value=f"Mostrando apenas os primeiros 10 de {len(lista_arquivos)} vídeos.", 
            inline=False
        )
    
    embed.add_field(
        name="🗑️ Aviso Importante",
        value="⚠️ **Após o upload bem-sucedido, todos os arquivos deste vídeo serão EXCLUÍDOS automaticamente!**",
        inline=False
    )
    
    embed.set_footer(text=f"Reaja com o número correspondente ao vídeo (tempo limite: {TIMEOUT_INTERACOES//60} minutos)")
    
    if interaction:
        mensagem = await interaction.followup.send(embed=embed, wait=True)
    else:
        mensagem = await ctx.send(embed=embed)
    
    ultima_mensagem_status = mensagem
    
    # Adicionar reações
    for i in range(min(len(lista_arquivos), 10)):
        await mensagem.add_reaction(emojis_numeros[i])
    
    # Armazenar seleção ativa
    selecoes_ativas[mensagem.id] = {
        'arquivos': lista_arquivos,
        'autor': author.id,
        'interaction': interaction
    }

async def mostrar_status_sistema(interaction=None, ctx=None):
    """Mostra status do sistema (para reação ou comando)"""
    global ultima_mensagem_status
    
    channel = interaction.channel if interaction else ctx.channel
    
    arquivos = listar_arquivos_vinculados()
    
    # Verificar configurações
    tem_discord_token = bool(DISCORD_BOT_TOKEN)
    tem_deepseek_key = bool(DEEPSEEK_API_KEY)
    tem_credentials = os.path.exists('credentials.json')
    tem_token = os.path.exists('token.json')
    
    embed = discord.Embed(
        title="📊 Status do Sistema",
        color=0x0099ff
    )
    
    embed.add_field(name="📁 Pasta de Vídeos", value=PASTA_VIDEOS, inline=False)
    embed.add_field(name="🎬 Vídeos Prontos", value=str(len(arquivos)), inline=True)
    embed.add_field(name="🤖 Bot Online", value="✅" if bot.is_ready() else "❌", inline=True)
    
    # Status da fila
    status_fila = f"Upload em andamento: {'✅' if upload_em_andamento else '❌'}\n"
    status_fila += f"Vídeos na fila: `{fila_uploads.qsize()}`\n"
    status_fila += f"Fila ativa: {'✅' if hasattr(bot, 'gerenciador_fila_iniciado') else '❌'}"
    
    embed.add_field(name="🔄 Status da Fila", value=status_fila, inline=False)
    
    # Status das configurações
    config_status = f"Discord Token: {'✅' if tem_discord_token else '❌'}\n"
    config_status += f"DeepSeek API: {'✅' if tem_deepseek_key else '❌'}\n"
    config_status += f"Credentials: {'✅' if tem_credentials else '❌'}\n"
    config_status += f"Token YouTube: {'✅' if tem_token else '❌'}"
    
    embed.add_field(name="⚙️ Configurações", value=config_status, inline=False)
    
    # Informações de tempo
    embed.add_field(
        name="⏱️ Configurações de Tempo", 
        value=f"Tempo limite entre interações: **{TIMEOUT_INTERACOES//60} minutos**", 
        inline=False
    )
    
    # Informações de arquivos obrigatórios
    videos_sem_contexto = sum(1 for info in arquivos.values() if not info.get('contexto'))
    videos_sem_thumb = sum(1 for info in arquivos.values() if not info.get('thumb'))
    
    embed.add_field(
        name="📋 Status dos Arquivos",
        value=(
            f"Vídeos sem contexto: `{videos_sem_contexto}`\n"
            f"Vídeos sem thumbnail: `{videos_sem_thumb}`\n"
            f"Total de vídeos: `{len(arquivos)}`"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🗑️ Limpeza Automática",
        value="✅ **ATIVADA** - Arquivos são excluídos automaticamente após upload bem-sucedido",
        inline=False
    )
    
    view = ViewComHome(timeout=TIMEOUT_INTERACOES)
    
    if interaction:
        mensagem = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
    else:
        mensagem = await ctx.send(embed=embed, view=view)
    
    ultima_mensagem_status = mensagem

# ========== FUNÇÕES EXISTENTES (mantidas) ==========

def extrair_info_arquivo(nome_arquivo):
    """Extrai nome do jogo e número do episódio do nome do arquivo"""
    # Remover extensão do arquivo
    nome_base = os.path.splitext(nome_arquivo)[0]
    
    # Padrões comuns para detectar episódios
    padroes_episodio = [
        r'[Ee]p[._\s]*(\d+)',  # Ep 1, Ep.1, Ep_1
        r'[Ee]pisodio[._\s]*(\d+)',  # Episodio 1
        r'[Pp]arte[._\s]*(\d+)',  # Parte 1
        r'[\._\-](\d+)[\._\-]',  # -01-, _01_
        r'\s(\d+)\s',  # espaço 1 espaço
    ]
    
    numero_episodio = None
    nome_jogo = nome_base
    
    # Tentar encontrar número do episódio
    for padrao in padroes_episodio:
        match = re.search(padrao, nome_base)
        if match:
            numero_episodio = int(match.group(1))
            # Remover a parte do episódio do nome do jogo
            nome_jogo = re.sub(padrao, '', nome_base).strip()
            # Limpar caracteres especiais no final
            nome_jogo = re.sub(r'[\._\-]\s*$', '', nome_jogo).strip()
            break
    
    # Se não encontrou padrão específico, tentar encontrar números no final
    if numero_episodio is None:
        match = re.search(r'(\d+)$', nome_base)
        if match:
            numero_episodio = int(match.group(1))
            nome_jogo = nome_base[:-len(match.group(1))].strip()
            nome_jogo = re.sub(r'[\._\-]\s*$', '', nome_jogo).strip()
    
    return nome_jogo, numero_episodio

def autenticar_youtube():
    """Autentica com a API do YouTube usando credentials.json"""
    creds = None
    
    # token.json armazena os tokens de acesso/refresh
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    # Se não há credenciais válidas, faz o fluxo OAuth
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                print("❌ credentials.json não encontrado")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Salva as credenciais para a próxima execução
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return build('youtube', 'v3', credentials=creds)

def listar_arquivos_vinculados():
    """Lista vídeos e arquivos relacionados com o mesmo nome base"""
    arquivos = {}
    padroes = ["*.mp4", "*.avi", "*.mkv", "*.mov", "*.srt", "*.jpg", "*.jpeg", "*.png", "*.txt", "*.json"]
    
    for padrao in padroes:
        for caminho in glob.glob(os.path.join(PASTA_VIDEOS, "**", padrao), recursive=True):
            nome_base = os.path.splitext(os.path.basename(caminho))[0]
            extensao = os.path.splitext(caminho)[1].lower()
            
            if nome_base not in arquivos:
                arquivos[nome_base] = {"nome_base": nome_base}
            
            if extensao in ['.mp4', '.avi', '.mkv', '.mov']:
                arquivos[nome_base]['video'] = caminho
            elif extensao == '.srt':
                arquivos[nome_base]['legendas'] = caminho
            elif extensao in ['.jpg', '.jpeg', '.png']:
                arquivos[nome_base]['thumb'] = caminho
            elif extensao in ['.txt', '.json']:
                arquivos[nome_base]['contexto'] = caminho
                
    # Retorna apenas os que têm vídeo
    return {k: v for k, v in arquivos.items() if 'video' in v}

async def gerar_metadados_deepseek(contexto, nome_jogo, numero_episodio):
    """Gera título e descrição otimizados para gameplay usando a API do DeepSeek"""
    if not DEEPSEEK_API_KEY:
        titulo_fallback = f"🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: Aventura Inicial"
        return titulo_fallback, f"""🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: Aventura Inicial

📖 Sinopse
Embarque nesta jornada emocionante cheia de desafios e descobertas. A cada episódio, novas aventuras aguardam!

🎯 Neste Episódio:
• 🎮 Gameplay intenso e envolvente
• 🗺️ Exploração de novos territórios
• ⚔️ Desafios estratégicos e emocionantes
• 🎯 Objetivos e missões desafiadoras
• 💡 Momentos épicos e inesquecíveis

💡 Uma aventura que vai te prender do início ao fim!

🔖 #Gameplay #Gaming #GameplayPTBR #Viral #Games #Jogos #GameplayBrazil"""

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }

    # Preparar informações do episódio para o prompt
    info_episodio = f"Nome do Jogo: {nome_jogo}\n"
    if numero_episodio:
        info_episodio += f"Número do Episódio: {numero_episodio}\n"
    info_episodio += f"Contexto Adicional: {contexto}"

    prompt = f"""
    COM BASE NAS INFORMAÇÕES ABAIXO, GERAR METADADOS OTIMIZADOS PARA VÍDEO DE GAMEPLAY:

    INFORMAÇÕES DO EPISÓDIO:
    {info_episodio}

    REGRAS ESTRITAS PARA FORMATAÇÃO:

    TÍTULO (OBRIGATÓRIO):
    - SEMPRE iniciar com: "🎮 [NOME DO JOGO] - Episódio [X]:"
    - [X] = número sequencial do episódio ({numero_episodio if numero_episodio else "use 1 se não especificado"})
    - [NOME DO JOGO] = "{nome_jogo}"
    - Título criativo: 3-5 palavras que capturem a essência sem spoilers
    - Tom: misterioso e convidativo

    DESCRIÇÃO (ESTRUTURA EXATA):
    🎮 [NÃO INCLUA O TÍTULO AQUI]
    
    📖 SINOPSE
    [4-6 linhas descrevendo contexto e atmosfera]
    [Introduzir conflito sem spoilers]
    [Estabelecer progressão emocional]
    
    🎯 NESTE EPISÓDIO:
    • [Item 1 com emoji relevante]
    • [Item 2 com emoji relevante] 
    • [Item 3 com emoji relevante]
    • [Item 4 com emoji relevante]
    • [Item 5 com emoji relevante]
    
    💡 [Frase de impacto em 1 linha - gancho emocional]
    
    🔖 HASHTAGS
    [6-8 hashtags prioritárias incluindo #Gameplay e variações]

    LÓGICA DE CONTEÚDO:
    - Usar o nome do jogo fornecido: "{nome_jogo}"
    - Usar número do episódio: {numero_episodio if numero_episodio else "1 (padrão)"}
    - Mapear elementos-chave: personagens, locais, mecânicas, progressão emocional
    - Evitar completamente spoilers
    - Usar tom envolvente e misterioso
    - Incluir 5-8 palavras-chave semanticamente relacionadas

    FORMATO DE RESPOSTA (SEGUIR EXATAMENTE):
    TITULO: 🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: [Título Criativo 3-5 palavras]
    DESCRICAO: 
    🎮 [Título Completo]
    
    📖 Sinopse
    [4-6 linhas aqui]
    
    🎯 Neste Episódio:
    • [Item 1]
    • [Item 2]
    • [Item 3]
    • [Item 4]
    • [Item 5]
    
    💡 [Frase de impacto]
    
    🔖 [Hashtags aqui]
    """

    data = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 2000,
        "temperature": 0.7
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            resultado = response.json()
            resposta = resultado['choices'][0]['message']['content']
            
            # Processar resposta
            linhas = resposta.split('\n')
            titulo = None
            descricao = ""
            em_descricao = False
            
            for linha in linhas:
                if linha.startswith('TITULO: '):
                    titulo = linha.replace('TITULO: ', '').strip()
                elif linha.startswith('DESCRICAO: '):
                    descricao = linha.replace('DESCRICAO: ', '').strip()
                    em_descricao = True
                elif em_descricao:
                    if descricao:  # Se já começamos a descrição
                        descricao += "\n" + linha
                    else:
                        descricao = linha
            
            # Fallback se não conseguir parsear corretamente
            if not titulo or not descricao:
                titulo_fallback = f"🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: Aventura Inicial"
                descricao_fallback = f"""🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: Aventura Inicial

📖 Sinopse
Embarque nesta jornada épica de {nome_jogo}! Explore mundos incríveis, enfrente desafios emocionantes e descubra segredos ocultos.

🎯 Neste Episódio:
• 🎮 Gameplay envolvente e dinâmico
• 🗺️ Exploração de ambientes impressionantes
• ⚔️ Desafios estratégicos e intensos
• 🎯 Objetivos emocionantes e recompensadores
• 💡 Revelações surpreendentes

💡 Uma aventura que vai te prender da primeira à última cena!

🔖 #{nome_jogo.replace(' ', '')} #Gameplay #Gaming #GameplayPTBR #Viral #Games"""
                
                return titulo_fallback, descricao_fallback

            return titulo, descricao
            
        else:
            print(f"Erro API DeepSeek: {response.status_code} - {response.text}")
            titulo_fallback = f"🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: Aventura Inicial"
            descricao_fallback = f"""🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: Aventura Inicial

📖 Sinopse
Uma nova jornada em {nome_jogo} está prestes a começar! Acompanhe esta aventura única cheia de emoções.

🎯 Neste Episódio:
• 🎮 Gameplay de alta qualidade
• 🗺️ Descobertas impressionantes
• ⚔️ Desafios emocionantes
• 🎯 Objetivos importantes
• 💡 Momentos memoráveis

💡 Não perca esta aventura épica!

🔖 #{nome_jogo.replace(' ', '')} #Gameplay #Gaming #Viral #GameplayBrazil"""
            
            return titulo_fallback, descricao_fallback
            
    except Exception as e:
        print(f"Erro ao chamar DeepSeek: {e}")
        titulo_fallback = f"🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: Aventura Inicial"
        descricao_fallback = f"""🎮 {nome_jogo} - Episódio {numero_episodio if numero_episodio else 1}: Aventura Inicial

📖 Sinopse
Embarque nesta incrível aventura em {nome_jogo}! Cada episódio traz novas surpresas e desafios.

🎯 Neste Episódio:
• 🎮 Experiência de jogo imersiva
• 🗺️ Ambientes ricos e detalhados
• ⚔️ Combates e estratégias únicas
• 🎯 Progressão emocionante
• 💡 Histórias cativantes

💡 Prepare-se para horas de diversão!

🔖 #{nome_jogo.replace(' ', '')} #Gameplay #Gaming #Viral #Games"""
        
        return titulo_fallback, descricao_fallback

async def atualizar_status_upload(ctx, message_id, etapa, progresso=0, total=100, detalhes=""):
    """Atualiza o status do upload em tempo real"""
    global ultima_mensagem_status
    
    try:
        # Calcular porcentagem
        porcentagem = (progresso / total) * 100 if total > 0 else 0
        
        # Criar barra de progresso
        barra_length = 20
        blocos_preenchidos = int(barra_length * porcentagem / 100)
        barra = "█" * blocos_preenchidos + "░" * (barra_length - blocos_preenchidos)
        
        # Definir cor baseado no progresso
        cor = 0x00ff00 if porcentagem == 100 else 0x0099ff if porcentagem >= 50 else 0xff9900 if porcentagem >= 25 else 0xff0000
        
        embed = discord.Embed(
            title="📤 Status do Upload - YouTube",
            description=f"**Etapa:** {etapa}\n{detalhes}",
            color=cor,
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="📊 Progresso",
            value=f"```[{barra}] {porcentagem:.1f}%```\n{progresso}/{total}",
            inline=False
        )
        
        # Atualizar a mensagem
        try:
            mensagem = await ctx.channel.fetch_message(message_id)
            await mensagem.edit(embed=embed)
            ultima_mensagem_status = mensagem
        except discord.NotFound:
            print("Mensagem de status não encontrada, criando nova...")
            nova_mensagem = await ctx.send(embed=embed)
            ultima_mensagem_status = nova_mensagem
        
    except Exception as e:
        print(f"Erro ao atualizar status: {e}")

async def upload_youtube_real(ctx, status_message, video_path, titulo, descricao, thumbnail_path=None, agendar=None):
    """Faz upload real para o YouTube usando a API com status em tempo real"""
    global ultima_mensagem_status
    
    try:
        # Etapa 1: Autenticação
        await atualizar_status_upload(ctx, status_message.id, "🔐 Autenticando com YouTube", 10, 100, "Conectando à API do YouTube...")
        
        youtube = autenticar_youtube()
        if not youtube:
            await atualizar_status_upload(ctx, status_message.id, "❌ Falha na autenticação", 0, 100, "Não foi possível autenticar com o YouTube")
            return {"status": "erro", "mensagem": "Falha na autenticação do YouTube"}
        
        # Etapa 2: Preparando upload
        await atualizar_status_upload(ctx, status_message.id, "📦 Preparando upload", 25, 100, "Configurando metadados do vídeo...")
        
        # Configurações do vídeo
        body = {
            'snippet': {
                'title': titulo,
                'description': descricao,
                'tags': [],  # Você pode adicionar tags aqui
                'categoryId': '22'  # Categoria "People & Blogs"
            },
            'status': {
                'privacyStatus': 'private',  # Inicialmente privado
                'selfDeclaredMadeForKids': False
            }
        }
        
        # Se há agendamento, configura a data de publicação
        if agendar:
            body['status']['publishAt'] = agendar
            body['status']['privacyStatus'] = 'private'  # Será público no agendamento
        else:
            body['status']['privacyStatus'] = 'public'
        
        # Etapa 3: Iniciando upload
        await atualizar_status_upload(ctx, status_message.id, "⏫ Iniciando upload do vídeo", 40, 100, "Enviando arquivo de vídeo...")
        
        # Faz o upload do vídeo com monitoramento de progresso
        file_size = os.path.getsize(video_path)
        media = MediaFileUpload(video_path, chunksize=1024*1024, resumable=True)  # 1MB chunks
        
        request = youtube.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=media
        )
        
        # Etapa 4: Upload em progresso - MELHORIA: Monitoramento real do progresso
        response = None
        last_update = datetime.now()
        chunk_count = 0
        
        while response is None:
            status, response = request.next_chunk()
            chunk_count += 1
            
            if status:
                # Progresso baseado no número de chunks processados (estimativa)
                progresso_estimado = min(40 + (chunk_count * 2), 90)  # Progresso de 40% a 90%
                
                # Atualizar a cada 5 chunks ou a cada 10 segundos
                if chunk_count % 5 == 0 or (datetime.now() - last_update).seconds >= 10:
                    await atualizar_status_upload(
                        ctx, status_message.id, 
                        "📤 Upload em andamento", 
                        progresso_estimado, 100, 
                        f"Processando... ({chunk_count} chunks enviados)"
                    )
                    last_update = datetime.now()
            else:
                # Se não há status, ainda estamos no início do upload
                if chunk_count % 10 == 0:
                    progresso_estimado = min(40 + (chunk_count * 1), 85)
                    await atualizar_status_upload(
                        ctx, status_message.id,
                        "📤 Preparando upload",
                        progresso_estimado, 100,
                        "Inicializando transmissão de dados..."
                    )
        
        video_id = response['id']
        print(f"Vídeo enviado com ID: {video_id}")
        
        # Etapa 5: Processamento no YouTube
        await atualizar_status_upload(ctx, status_message.id, "🔧 Processamento no YouTube", 92, 100, "YouTube está processando o vídeo...")
        
        # Simular progresso do processamento
        for i in range(3):
            await asyncio.sleep(1)
            await atualizar_status_upload(
                ctx, status_message.id,
                "🔧 Processamento no YouTube",
                92 + (i * 2), 100,
                f"Processando vídeo... ({i+1}/3)"
            )
        
        # Upload da thumbnail se existir - COM TRATAMENTO DE ERRO MELHORADO
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                await atualizar_status_upload(ctx, status_message.id, "🖼️ Enviando thumbnail", 98, 100, "Enviando imagem de thumbnail...")
                
                # Aguardar um pouco para garantir que o vídeo esteja processado
                await asyncio.sleep(5)
                
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path)
                ).execute()
                print("Thumbnail definida com sucesso.")
            except HttpError as e:
                if e.resp.status == 404:
                    print(f"Erro 404 ao definir thumbnail: Vídeo ainda não está disponível. Tentando novamente em 10 segundos...")
                    await asyncio.sleep(10)
                    try:
                        youtube.thumbnails().set(
                            videoId=video_id,
                            media_body=MediaFileUpload(thumbnail_path)
                        ).execute()
                        print("Thumbnail definida com sucesso na segunda tentativa.")
                    except HttpError as e2:
                        print(f"Erro ao definir thumbnail na segunda tentativa: {e2}")
                else:
                    print(f"Erro ao definir thumbnail: {e}")
            except Exception as e:
                print(f"Erro ao definir thumbnail: {e}")
        
        # Etapa 6: Concluído
        await atualizar_status_upload(ctx, status_message.id, "✅ Upload concluído!", 100, 100, "Vídeo publicado com sucesso!")
        
        return {
            "status": "sucesso",
            "video_id": video_id,
            "url": f"https://youtube.com/watch?v={video_id}"
        }
        
    except Exception as e:
        print(f"Erro no upload do YouTube: {e}")
        await atualizar_status_upload(ctx, status_message.id, "❌ Erro no upload", 0, 100, f"Erro: {str(e)}")
        return {"status": "erro", "mensagem": str(e)}

def gerar_opcoes_agendamento():
    """Gera opções de agendamento para os próximos 24 dias (limite do Discord: 25 opções)"""
    opcoes = []
    hoje = datetime.now()
    
    # Opção de publicação imediata
    opcoes.append(discord.SelectOption(
        label="🚀 Publicação Imediata",
        description="Publicar o vídeo assim que o upload terminar",
        value="imediato",
        emoji="🚀"
    ))
    
    # Opção para hoje ao meio-dia (se ainda não passou)
    hoje_meio_dia = hoje.replace(hour=12, minute=0, second=0, microsecond=0)
    if hoje_meio_dia > hoje:
        opcoes.append(discord.SelectOption(
            label=f"⏰ Hoje às 12:00",
            description=hoje_meio_dia.strftime("%d/%m/%Y %H:%M"),
            value=hoje_meio_dia.strftime("%Y-%m-%dT%H:%M:%S"),
            emoji="⏰"
        ))
    
    # Gerar opções para os próximos 23 dias (totalizando 25 opções com a publicação imediata e hoje)
    dias_restantes = 23
    
    for i in range(1, dias_restantes + 1):
        data = hoje + timedelta(days=i)
        data_meio_dia = data.replace(hour=12, minute=0, second=0, microsecond=0)
        
        # Formatar a label de forma amigável
        if i == 1:
            label = "📅 Amanhã às 12:00"
        elif i <= 7:
            # Dias da semana para a primeira semana
            dias_semana = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
            label = f"📅 {dias_semana[data.weekday()]} às 12:00"
        elif i <= 14:
            # Segunda semana com indicação
            label = f"🗓️ Próxima {data.strftime('%A')} - {data_meio_dia.strftime('%d/%m')}"
        else:
            # Datas mais distantes
            semanas = (i + 6) // 7  # Calcula o número de semanas
            label = f"🗓️ Em {semanas} semana(s) - {data_meio_dia.strftime('%d/%m')}"
        
        opcoes.append(discord.SelectOption(
            label=label,
            description=data_meio_dia.strftime("%d/%m/%Y às %H:%M"),
            value=data_meio_dia.strftime("%Y-%m-%dT%H:%M:%S"),
            emoji="📅" if i <= 7 else "🗓️"
        ))
        
        # Parar quando atingir 25 opções (já incluímos a publicação imediata)
        if len(opcoes) >= 25:
            break
    
    return opcoes

# ========== NOVAS FUNÇÕES PARA VERIFICAÇÃO DE ARQUIVOS OBRIGATÓRIOS ==========

async def verificar_arquivos_obrigatorios(ctx, video_info):
    """Verifica se os arquivos obrigatórios (contexto e thumbnail) existem"""
    nome_base = video_info['nome_base']
    
    # Verificar arquivo de contexto
    if not video_info.get('contexto'):
        await ctx.send(f"⚠️ **ARQUIVO DE CONTEXTO OBRIGATÓRIO**\n"
                      f"O vídeo `{nome_base}` não possui arquivo de contexto (.txt).\n\n"
                      f"**Por favor, envie o arquivo de contexto** contendo informações sobre o episódio:")
        
        def check_arquivo_contexto(m):
            return (m.author == ctx.author and 
                   m.channel == ctx.channel and 
                   m.attachments and 
                   any(att.filename.lower().endswith('.txt') for att in m.attachments))
        
        try:
            msg_contexto = await bot.wait_for('message', timeout=TIMEOUT_INTERACOES, check=check_arquivo_contexto)
            arquivo_contexto = msg_contexto.attachments[0]
            
            # Salvar o arquivo de contexto
            caminho_contexto = os.path.join(PASTA_VIDEOS, f"{nome_base}.txt")
            await arquivo_contexto.save(caminho_contexto)
            video_info['contexto'] = caminho_contexto
            
            await ctx.send(f"✅ **Arquivo de contexto salvo:** `{caminho_contexto}`")
            
        except asyncio.TimeoutError:
            await ctx.send("⏰ Tempo esgotado para envio do arquivo de contexto. Processo cancelado.")
            return False
    
    # Verificar arquivo de thumbnail
    if not video_info.get('thumb'):
        await ctx.send(f"⚠️ **ARQUIVO OF THUMBNAIL OBRIGATÓRIO**\n"
                      f"O vídeo `{nome_base}` não possui arquivo de thumbnail (.jpg, .png, .jpeg).\n\n"
                      f"**Por favor, envie a imagem de thumbnail:**")
        
        def check_arquivo_thumbnail(m):
            return (m.author == ctx.author and 
                   m.channel == ctx.channel and 
                   m.attachments and 
                   any(att.filename.lower().endswith(('.jpg', '.jpeg', '.png')) for att in m.attachments))
        
        try:
            msg_thumbnail = await bot.wait_for('message', timeout=TIMEOUT_INTERACOES, check=check_arquivo_thumbnail)
            arquivo_thumbnail = msg_thumbnail.attachments[0]
            
            # Salvar o arquivo de thumbnail
            extensao = os.path.splitext(arquivo_thumbnail.filename)[1].lower()
            caminho_thumbnail = os.path.join(PASTA_VIDEOS, f"{nome_base}{extensao}")
            await arquivo_thumbnail.save(caminho_thumbnail)
            video_info['thumb'] = caminho_thumbnail
            
            await ctx.send(f"✅ **Arquivo de thumbnail salvo:** `{caminho_thumbnail}`")
            
        except asyncio.TimeoutError:
            await ctx.send("⏰ Tempo esgotado para envio do arquivo de thumbnail. Processo cancelado.")
            return False
    
    return True

async def mostrar_status_arquivos(ctx, video_info):
    """Mostra o status dos arquivos obrigatórios e opcionais"""
    nome_base = video_info['nome_base']
    
    embed = discord.Embed(
        title="📋 Status dos Arquivos do Vídeo",
        description=f"Verificação dos arquivos para: `{nome_base}`",
        color=0x0099ff
    )
    
    # Status do arquivo de contexto
    if video_info.get('contexto'):
        embed.add_field(
            name="📄 Arquivo de Contexto", 
            value="✅ **PRESENTE** - Informações disponíveis para geração de metadados",
            inline=False
        )
    else:
        embed.add_field(
            name="📄 Arquivo de Contexto", 
            value="❌ **FALTANDO** - Arquivo .txt com informações do episódio",
            inline=False
        )
    
    # Status do arquivo de thumbnail
    if video_info.get('thumb'):
        embed.add_field(
            name="🖼️ Arquivo de Thumbnail", 
            value="✅ **PRESENTE** - Imagem disponível para o vídeo",
            inline=False
        )
    else:
        embed.add_field(
            name="🖼️ Arquivo de Thumbnail", 
            value="❌ **FALTANDO** - Arquivo de imagem (.jpg, .png, .jpeg)",
            inline=False
        )
    
    # Status de arquivos opcionais
    if video_info.get('legendas'):
        embed.add_field(
            name="🎯 Legendas", 
            value="✅ **PRESENTE** - Arquivo de legendas disponível",
            inline=True
        )
    else:
        embed.add_field(
            name="🎯 Legendas", 
            value="⚪ **OPCIONAL** - Arquivo de legendas não encontrado",
            inline=True
        )
    
    embed.add_field(
        name="🗑️ Aviso de Limpeza",
        value="⚠️ **Todos estes arquivos serão EXCLUÍDOS automaticamente após o upload bem-sucedido!**",
        inline=False
    )
    
    view = ViewComHome(timeout=TIMEOUT_INTERACOES)
    await ctx.send(embed=embed, view=view)

# ========== EVENTOS DO BOT ==========

@bot.event
async def on_ready():
    global ultima_mensagem_status
    
    print(f'🤖 Bot conectado como {bot.user}')
    
    # Iniciar gerenciador de fila
    if not hasattr(bot, 'gerenciador_fila_iniciado'):
        bot.gerenciador_fila_iniciado = True
        bot.loop.create_task(gerenciador_fila_uploads())
        print('🔄 Gerenciador de fila de uploads iniciado')
    
    # Verificar se o bot tem acesso ao canal específico
    canal = bot.get_channel(CANAL_DISCORD_ID)
    if canal:
        print(f'📢 Bot está pronto para receber comandos no canal: {canal.name}')
        
        # CORREÇÃO: Chamar a função corretamente passando o canal
        await mostrar_menu_principal(channel=canal)
        
        # Criar mensagem global da fila
        await atualizar_fila_global()
    else:
        print(f'❌ Não foi possível acessar o canal com ID: {CANAL_DISCORD_ID}')

@bot.event
async def on_reaction_add(reaction, user):
    """Processa reações em mensagens do bot"""
    global ultima_mensagem_status
    
    if user.bot:
        return
    
    # Verificar se é uma reação em uma mensagem de seleção de vídeos
    if reaction.message.id in selecoes_ativas:
        selecao = selecoes_ativas[reaction.message.id]
        
        if user.id != selecao['autor']:
            return
        
        # Emojis numéricos
        emojis_numeros = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        
        if str(reaction.emoji) in emojis_numeros:
            indice = emojis_numeros.index(str(reaction.emoji))
            lista_arquivos = selecao['arquivos']
            
            if indice < len(lista_arquivos):
                nome_selecionado, video_info = lista_arquivos[indice]
                
                # Limpar seleção
                del selecoes_ativas[reaction.message.id]
                
                # Processar vídeo selecionado
                ctx = selecao.get('interaction')
                if ctx:
                    # Se veio de uma interação por botão
                    class ContextSimulado:
                        def __init__(self, interaction):
                            self.channel = interaction.channel
                            self.author = interaction.user
                            self.send = interaction.followup.send
                    ctx_simulado = ContextSimulado(ctx)
                    await processar_video_selecionado(ctx_simulado, nome_selecionado, video_info)
                else:
                    # Se veio de um comando tradicional
                    await processar_video_selecionado(reaction.message.channel, nome_selecionado, video_info)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Comando não encontrado. Use `!comandos` para ver todos os comandos disponíveis.")
    elif isinstance(error, commands.CheckFailure):
        # Não faz nada para erros de canal incorreto, já que a mensagem já foi enviada
        pass
    else:
        await ctx.send(f"❌ Erro: {str(error)}")

# ========== COMANDOS TRADICIONAIS (mantidos para compatibilidade) ==========

@bot.command()
@verificar_canal_correto()
async def comandos(ctx):
    """Mostra todos os comandos disponíveis"""
    await mostrar_comandos(ctx=ctx)

@bot.command()
@verificar_canal_correto()
async def listar(ctx):
    """Lista vídeos para seleção por reação"""
    await listar_videos_reacao(ctx=ctx)

@bot.command()
@verificar_canal_correto()
async def fila(ctx):
    """Mostra o status atual da fila de uploads"""
    await mostrar_fila_detalhada(ctx=ctx)

@bot.command()
@verificar_canal_correto()
async def status(ctx):
    """Mostra status do sistema"""
    await mostrar_status_sistema(ctx=ctx)

@bot.command()
@verificar_canal_correto()
async def home(ctx):
    """Volta ao menu principal"""
    await mostrar_menu_principal(ctx=ctx)

@bot.command()
@verificar_canal_correto()
@commands.is_owner()
async def auth_youtube(ctx):
    """Força reautenticação com YouTube (apenas dono)"""
    try:
        await ctx.send("🔄 Iniciando autenticação com YouTube...")
        youtube = autenticar_youtube()
        if youtube:
            await ctx.send("✅ Autenticação com YouTube realizada com sucesso!")
        else:
            await ctx.send("❌ Falha na autenticação do YouTube")
    except Exception as e:
        await ctx.send(f"❌ Erro na autenticação: {e}")

@bot.command()
@verificar_canal_correto()
@commands.is_owner()
async def limpar_fila(ctx):
    """Limpa a fila de uploads (apenas dono)"""
    global fila_uploads, fila_ativa
    
    if fila_uploads.empty() and not upload_em_andamento:
        await ctx.send("📭 A fila já está vazia.")
        return
    
    # Criar nova fila vazia
    while not fila_uploads.empty():
        try:
            fila_uploads.get_nowait()
            fila_uploads.task_done()
        except:
            break
    
    fila_ativa.clear()
    
    await ctx.send("🗑️ **Fila limpa!** Todos os uploads pendentes foram removidos.")
    await atualizar_fila_global()

# ========== PROCESSAMENTO DE VÍDEOS (funções atualizadas) ==========

async def processar_edicao_metadados(ctx, video_info, titulo_original, descricao_original):
    """Processa a edição dos metadados pelo usuário"""
    view = ValidacaoView(timeout=TIMEOUT_INTERACOES)
    embed_validacao = discord.Embed(
        title="✏️ Validação de Metadados - REVISÃO OBRIGATÓRIA",
        description="**Revise e valide os metadados gerados antes do upload:**",
        color=0xff9900
    )
    
    embed_validacao.add_field(name="🎬 Título Gerado", value=f"```{titulo_original}```", inline=False)
    
    # Mostrar descrição de forma organizada
    linhas_descricao = descricao_original.split('\n')
    descricao_preview = ""
    for linha in linhas_descricao[:15]:  # Limitar preview
        if linha.strip():
            descricao_preview += f"{linha}\n"
    
    if len(linhas_descricao) > 15:
        descricao_preview += "...\n*(descrição continua)*"
    
    embed_validacao.add_field(name="📝 Descrição Gerada", value=f"```{descricao_preview}```", inline=False)
    
    embed_validacao.add_field(
        name="📋 Opções de Validação",
        value=(
            "**✅ Aprovar Tudo** - Usar metadados como estão\n"
            "**✏️ Editar Título** - Modificar apenas o título\n"
            "**📝 Editar Descrição** - Modificar apenas a descrição\n"
            "**❌ Cancelar** - Cancelar upload completamente"
        ),
        inline=False
    )
    
    embed_validacao.add_field(
        name="🗑️ Aviso Importante",
        value="⚠️ **Após o upload bem-sucedido, todos os arquivos deste vídeo serão EXCLUÍDOS automaticamente!**",
        inline=False
    )
    
    mensagem_validacao = await ctx.send(embed=embed_validacao, view=view)
    
    # Aguardar decisão do usuário
    await view.wait()
    
    titulo_final = titulo_original
    descricao_final = descricao_original
    
    if view.aprovado:
        await ctx.send("✅ **Metadados aprovados!** Continuando com o processo...")
        return titulo_final, descricao_final, True
    
    elif view.editar_titulo:
        await ctx.send("✏️ **Modo de edição de título ativado.** Envie o novo título no chat:")
        
        def check_titulo(m):
            return m.author == ctx.author and m.channel == ctx.channel
        
        try:
            msg_titulo = await bot.wait_for('message', timeout=300, check=check_titulo)
            titulo_final = msg_titulo.content
            await ctx.send(f"✅ **Novo título definido:**\n```{titulo_final}```")
            
            # Perguntar se quer editar a descrição também
            embed_confirmacao = discord.Embed(
                title="📝 Edição Concluída",
                description=f"**Título atualizado com sucesso!** Deseja editar a descrição também?",
                color=0x0099ff
            )
            embed_confirmacao.add_field(name="🎬 Novo Título", value=f"```{titulo_final}```", inline=False)
            
            class ConfirmacaoView(ViewComHome):
                def __init__(self, timeout=60):
                    super().__init__(timeout=timeout)
                    self.editar_desc = False
                
                @discord.ui.button(label="✅ Manter Descrição", style=discord.ButtonStyle.success)
                async def manter_desc(self, interaction: discord.Interaction, button: Button):
                    await interaction.response.send_message("✅ **Descrição mantida!** Continuando...", ephemeral=True)
                    self.stop()
                
                @discord.ui.button(label="📝 Editar Descrição", style=discord.ButtonStyle.primary)
                async def editar_desc(self, interaction: discord.Interaction, button: Button):
                    self.editar_desc = True
                    await interaction.response.send_message("📝 **Editando descrição...**", ephemeral=True)
                    self.stop()
            
            confirm_view = ConfirmacaoView(timeout=60)
            msg_confirm = await ctx.send(embed=embed_confirmacao, view=confirm_view)
            await confirm_view.wait()
            
            if confirm_view.editar_desc:
                await ctx.send("📄 **Envie a nova descrição no chat:**")
                try:
                    msg_descricao = await bot.wait_for('message', timeout=300, check=check_titulo)
                    descricao_final = msg_descricao.content
                    await ctx.send(f"✅ **Nova descrição definida!**")
                except asyncio.TimeoutError:
                    await ctx.send("⏰ Tempo esgotado para edição da descrição. Mantendo descrição original.")
            
            return titulo_final, descricao_final, True
            
        except asyncio.TimeoutError:
            await ctx.send("⏰ Tempo esgotado para edição do título. Mantendo título original.")
            return titulo_final, descricao_final, True
    
    elif view.editar_descricao:
        await ctx.send("📄 **Modo de edição de descrição ativado.** Envie a nova descrição no chat:")
        
        def check_descricao(m):
            return m.author == ctx.author and m.channel == ctx.channel
        
        try:
            msg_descricao = await bot.wait_for('message', timeout=300, check=check_descricao)
            descricao_final = msg_descricao.content
            await ctx.send(f"✅ **Nova descrição definida com sucesso!**")
            return titulo_final, descricao_final, True
            
        except asyncio.TimeoutError:
            await ctx.send("⏰ Tempo esgotado para edição da descrição. Mantendo descrição original.")
            return titulo_final, descricao_final, True
    
    else:
        await ctx.send("❌ **Upload cancelado pelo usuário.**")
        return None, None, False

async def processar_video_selecionado(ctx, nome_arquivo, video_info):
    """Processa um vídeo selecionado por reação - VERSÃO COM VERIFICAÇÃO DE ARQUIVOS OBRIGATÓRIOS"""
    global ultima_mensagem_status
    
    await ctx.send(f"🔄 Processando `{nome_arquivo}`...")
    
    # VERIFICAÇÃO OBRIGATÓRIA DE ARQUIVOS
    await mostrar_status_arquivos(ctx, video_info)
    
    # Verificar e solicitar arquivos obrigatórios faltantes
    arquivos_ok = await verificar_arquivos_obrigatorios(ctx, video_info)
    if not arquivos_ok:
        await ctx.send("❌ **Processo cancelado.** Arquivos obrigatórios não foram fornecidos.")
        return
    
    # Mostrar status final dos arquivos
    await ctx.send("✅ **Todos os arquivos obrigatórios estão presentes!** Continuando com o processamento...")
    
    # Extrair informações do arquivo
    nome_arquivo_video = os.path.basename(video_info.get('video', ''))
    nome_jogo, numero_episodio = extrair_info_arquivo(nome_arquivo_video)
    
    embed_info = discord.Embed(
        title="🔍 Informações Detectadas do Arquivo",
        color=0x0099ff
    )
    embed_info.add_field(name="🎮 Nome do Jogo", value=nome_jogo, inline=True)
    embed_info.add_field(name="📺 Episódio", value=numero_episodio if numero_episodio else "Não detectado", inline=True)
    embed_info.add_field(name="📁 Arquivo", value=nome_arquivo_video, inline=False)
    
    mensagem_info = await ctx.send(embed=embed_info)
    ultima_mensagem_status = mensagem_info
    
    # Ler contexto
    contexto = f"Jogo: {nome_jogo}"
    if numero_episodio:
        contexto += f" | Episódio: {numero_episodio}"
    
    if 'contexto' in video_info:
        try:
            with open(video_info['contexto'], 'r', encoding='utf-8') as f:
                contexto_adicional = f.read()
            contexto += f"\nContexto Adicional: {contexto_adicional}"
            await ctx.send("📄 Contexto adicional carregado com sucesso.")
        except Exception as e:
            await ctx.send(f"⚠️ Erro ao ler contexto adicional: {e}")
    
    # Gerar metadados com DeepSeek
    await ctx.send("🧠 Gerando título e descrição otimizados para gameplay...")
    titulo_gerado, descricao_gerada = await gerar_metadados_deepseek(contexto, nome_jogo, numero_episodio)
    
    # Validação humana dos metadados
    titulo_final, descricao_final, continuar = await processar_edicao_metadados(ctx, video_info, titulo_gerado, descricao_gerada)
    
    if not continuar:
        return
    
    # Mostrar preview final
    embed_final = discord.Embed(
        title="📋 Metadados Finais - Confirmados",
        description="**Metadados que serão usados no upload:**",
        color=0x00ff00
    )
    embed_final.add_field(name="🎬 Título Final", value=f"```{titulo_final}```", inline=False)
    
    # Mostrar parte da descrição final
    linhas_desc = descricao_final.split('\n')
    desc_preview = "\n".join(linhas_desc[:10])
    if len(linhas_desc) > 10:
        desc_preview += "\n\n... (continua)"
    
    embed_final.add_field(name="📝 Descrição Final", value=f"```{desc_preview}```", inline=False)
    
    embed_final.add_field(
        name="🗑️ Aviso Final",
        value="⚠️ **Lembre-se:** Após o upload bem-sucedido, todos os arquivos deste vídeo serão **excluídos automaticamente**!",
        inline=False
    )
    
    mensagem_final = await ctx.send(embed=embed_final)
    ultima_mensagem_status = mensagem_final
    
    # Confirmação de upload
    confirm_msg = await ctx.send("⚠️ **Como deseja publicar?**\n\n▶️ Publicação Imediata\n📅 Agendar Publicação\n❌ Cancelar")
    ultima_mensagem_status = confirm_msg
    
    await confirm_msg.add_reaction('▶️')  # Imediato
    await confirm_msg.add_reaction('📅')  # Agendar
    await confirm_msg.add_reaction('❌')  # Cancelar
    
    def check_confirmacao(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ['▶️', '📅', '❌'] and reaction.message.id == confirm_msg.id
    
    try:
        reaction, user = await bot.wait_for('reaction_add', timeout=TIMEOUT_INTERACOES, check=check_confirmacao)
        
        if str(reaction.emoji) == '❌':
            await ctx.send("❌ Upload cancelado.")
            return
            
        elif str(reaction.emoji) == '▶️':
            agendar = "imediato"
            
        elif str(reaction.emoji) == '📅':
            # Mostrar caixa de seleção para agendamento
            opcoes_agendamento = gerar_opcoes_agendamento()
            
            embed_agendamento = discord.Embed(
                title="📅 Selecione a Data de Publicação",
                description="**24 dias de opções** disponíveis, sempre ao **meio-dia** 🕛\n\nEscolha uma data abaixo:",
                color=0xff9900
            )
            
            embed_agendamento.add_field(
                name="🚀 Publicação Imediata",
                value="O vídeo será publicado assim que o upload terminar",
                inline=False
            )
            
            embed_agendamento.add_field(
                name="📅 Próximos Dias",
                value="Selecione uma data futura para agendamento automático",
                inline=False
            )
            
            embed_agendamento.set_footer(text="A publicação ocorrerá sempre às 12:00 para melhor engajamento")
            
            view = AgendamentoView(opcoes_agendamento, timeout=TIMEOUT_INTERACOES)
            mensagem_agendamento = await ctx.send(embed=embed_agendamento, view=view)
            ultima_mensagem_status = mensagem_agendamento
            
            # Aguardar a seleção
            await view.wait()
            
            if view.agendamento_selecionado is None:
                await ctx.send("⏰ Tempo esgotado para seleção de agendamento.")
                return
            
            agendar = view.agendamento_selecionado
            
            if agendar == "imediato":
                await ctx.send("🚀 **Publicação Imediata** selecionada")
            else:
                await ctx.send(f"📅 **Agendamento confirmado:** `{agendar}`")
        
        # ADICIONAR À FILA em vez de fazer upload imediato
        tarefa = await adicionar_na_fila(
            ctx,
            video_info,
            titulo_final,
            descricao_final,
            video_info.get('thumb'),
            agendar
        )
        
        # OFERECER PRÓXIMO PASSO APÓS ADICIONAR À FILA
        await asyncio.sleep(1)  # Pequena pausa para melhor UX
        await oferecer_proximo_passo(ctx)
            
    except asyncio.TimeoutError:
        await ctx.send(f"⏰ Tempo esgotado ({TIMEOUT_INTERACOES//60} minutos). Operação cancelada.")

# Executar o bot
if __name__ == "__main__":
    # Verificar se a pasta de vídeos existe
    if not os.path.exists(PASTA_VIDEOS):
        os.makedirs(PASTA_VIDEOS)
        print(f"📁 Pasta criada: {PASTA_VIDEOS}")
    
    print("🚀 Iniciando bot de automação YouTube...")
    print(f"📢 Canal do Discord: {CANAL_DISCORD_ID}")
    print(f"⏱️ Timeout de interações: {TIMEOUT_INTERACOES} segundos ({TIMEOUT_INTERACOES//60} minutos)")
    print("🔄 Sistema de fila de uploads ativado")
    print("🎯 Fluxo contínuo implementado - Sempre oferece próximo passo")
    print("🔧 Verificação de arquivos obrigatórios implementada")
    print("🏠 Botão Home adicionado em todas as páginas")
    print("🗑️ Limpeza automática de arquivos após upload")
    print("📄 Contexto e Thumbnail são OBRIGATÓRIOS para cada vídeo")
    print("💡 Use os botões no canal ou comandos ! para interagir")
    
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        print("❌ Token do Discord inválido. Verifique a variável DISCORD_BOT_TOKEN no arquivo .env")
    except Exception as e:
        print(f"❌ Erro ao iniciar bot: {e}")