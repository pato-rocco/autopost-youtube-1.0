Documentação do Bot de Automação YouTube para Discord
📋 Índice
Visão Geral
Pré-requisitos
Configuração
Funcionalidades
Como Usar
Estrutura de Arquivos
Comandos
Sistema de Fila
Solução de Problemas
🎯 Visão Geral
Este é um bot Discord que automatiza completamente o processo de upload de vídeos para o YouTube. Ele gerencia desde a seleção dos vídeos até a publicação final, incluindo geração automática de metadados, agendamento e limpeza de arquivos.

⚙️ Pré-requisitos
Software Necessário
Python 3.7+ instalado
Conta no Discord com permissões para criar bot
Conta no Google Cloud com YouTube Data API v3 ativada
Conta no DeepSeek (opcional, para geração de metadados)
Bibliotecas Python
discord.py>=2.3.0
google-api-python-client>=2.80.0
google-auth-oauthlib>=1.0.0
google-auth-httplib2>=0.1.0
python-dotenv>=1.0.0
requests>=2.28.0
🔧 Configuração
1. Configuração do Bot Discord
Acesse Discord Developer Portal
Crie uma nova aplicação
Vá para "Bot" e crie um bot
Ative as intents:
MESSAGE CONTENT INTENT
SERVER MEMBERS INTENT
Copie o token do bot
2. Configuração do YouTube API
Acesse Google Cloud Console
Crie um projeto ou selecione um existente
Ative a YouTube Data API v3
Crie credenciais OAuth 2.0 para "Desktop Application"
Baixe o arquivo credentials.json
3. Configuração do DeepSeek (Opcional)
Acesse DeepSeek
Crie uma conta e gere uma API key
Use para melhorar a geração de títulos e descrições
4. Estrutura de Arquivos
pasta_do_bot/
├── 📄 youtube-auto-post-discord.py
├── 📄 iniciar_bot.bat
├── 📄 requirements.txt
├── 📄 .env
├── 📄 credentials.json
├── 📄 token.json (gerado automaticamente)
└── 📁 videos/
     ├── 📄 video1.mp4
     ├── 📄 video1.txt     # OBRIGATÓRIO
     ├── 📄 video1.jpg     # OBRIGATÓRIO
     └── 📄 video1.srt     # Opcional (legendas)
5. Arquivo .env
DISCORD_BOT_TOKEN=seu_token_do_discord_aqui
DEEPSEEK_API_KEY=sua_chave_do_deepseek_aqui
🚀 Funcionalidades
✅ Funcionalidades Principais
Sistema de Fila Inteligente: Uploads em background com processamento sequencial
Geração Automática de Metadados: Títulos e descrições otimizadas usando IA
Agendamento Flexível: Publicação imediata ou agendada (até 24 dias)
Verificação de Arquivos: Verifica arquivos obrigatórios antes do upload
Limpeza Automática: Exclui arquivos locais após upload bem-sucedido
Interface Intuitiva: Navegação por botões e comandos de texto
Status em Tempo Real: Monitoramento do progresso de uploads
Fluxo Contínuo: Sempre oferece próximo passo após cada ação
🔄 Fluxo de Trabalho
Preparação → Organize vídeos na pasta com arquivos obrigatórios
Seleção → Escolha vídeos via interface Discord
Validação → Revise e edite metadados gerados automaticamente
Agendamento → Defina data de publicação
Upload → Sistema de fila processa automaticamente
Limpeza → Arquivos excluídos após sucesso
📖 Como Usar
Primeira Execução
Execute iniciar_bot.bat (Windows) ou:
python youtube-auto-post-discord.py
Na primeira execução, será aberto um navegador para autenticação do YouTube
Autorize o aplicativo e volte ao terminal
Uso Diário
Método 1: Interface por Botões (Recomendado)
O bot automaticamente mostra o menu principal ao iniciar
Use os botões para navegar:
📋 Comandos - Lista de comandos disponíveis
🎬 Listar Vídeos - Ver vídeos disponíveis para upload
📊 Status da Fila - Ver uploads pendentes
⚙️ Status Sistema - Ver configurações e estatísticas
🏠 Home - Volta ao menu principal (sempre disponível)
Método 2: Comandos de Texto
!comandos    - Mostra comandos disponíveis
!listar      - Lista vídeos para upload
!fila        - Mostra status da fila
!status      - Mostra status do sistema
!home        - Volta ao menu principal
Processo de Upload Completo
1. Preparação dos Arquivos
Cada vídeo precisa de 3 arquivos na pasta videos/:

meu_video_ep1.mp4      # Vídeo principal
meu_video_ep1.txt      # Contexto (OBRIGATÓRIO)
meu_video_ep1.jpg      # Thumbnail (OBRIGATÓRIO)
meu_video_ep1.srt      # Legendas (OPCIONAL)
Arquivo de Contexto (.txt):

Neste episódio: Exploramos a floresta sombria, encontramos um tesouro antigo e enfrentamos nosso primeiro chefe.
Personagens: João, Maria, Guia Misterioso
Locais: Floresta Sombria, Templo Antigo
2. Seleção do Vídeo
Use !listar ou botão "🎬 Listar Vídeos"
Reaja com o número correspondente ao vídeo
O bot verifica automaticamente os arquivos obrigatórios
3. Geração de Metadados
O bot automaticamente:

Detecta nome do jogo e número do episódio
Gera título otimizado: "🎮 NomeJogo - Episódio X: Título Criativo"
Cria descrição estruturada com sinopse, tópicos e hashtags
4. Revisão e Edição
Opções disponíveis:

✅ Aprovar Tudo - Usa metadados gerados
✏️ Editar Título - Modifica apenas o título
📝 Editar Descrição - Modifica apenas a descrição
❌ Cancelar - Cancela o upload
5. Agendamento
Opções de publicação:

🚀 Imediata - Publica assim que o upload terminar
⏰ Hoje 12:00 - Se ainda não passou do meio-dia
📅 Datas Futuras - Até 24 dias no futuro, sempre às 12:00
6. Upload Automático
Vídeo é adicionado à fila
Upload acontece em background
Status em tempo real com barra de progresso
Thumbnail é enviada automaticamente
7. Pós-Upload
✅ Notificação de conclusão com link do vídeo
🗑️ Limpeza automática de todos os arquivos
🔄 Oferece próximo passo automaticamente
📊 Sistema de Fila
Características
Processamento Sequencial: Um upload por vez
Status em Tempo Real: Posição na fila e progresso
Resistente a Falhas: Continua após reinicializações
Background: Não bloqueia outras operações
Comandos de Gerenciamento
!fila                    - Status atual da fila
!limpar_fila             - Limpa toda a fila (apenas dono)
!auth_youtube            - Reautentica com YouTube (apenas dono)
⚠️ Solução de Problemas
Problemas Comuns
❌ Bot não inicia
Sintoma: Erro ao executar o script Solução:

Verifique se Python 3.7+ está instalado
Execute pip install -r requirements.txt
Confirme que o arquivo .env existe com DISCORD_BOT_TOKEN
❌ Autenticação do YouTube falha
Sintoma: Erro "Falha na autenticação do YouTube" Solução:

Verifique se credentials.json está na pasta correta
Execute !auth_youtube para reautenticar
Confirme que a YouTube Data API v3 está ativada
❌ Arquivos obrigatórios faltando
Sintoma: Bot solicita arquivos de contexto ou thumbnail Solução:

Certifique-se de que cada vídeo tem:
Arquivo .txt com contexto
Arquivo de imagem (.jpg, .png, .jpeg) para thumbnail
Os arquivos devem ter o mesmo nome base do vídeo
❌ Timeout de interações
Sintoma: "Tempo esgotado" após 5 minutos Solução:

Reinicie o processo clicando no botão correspondente
O timeout é de 5 minutos por segurança
❌ Upload muito lento
Sintoma: Upload demora muito tempo Solução:

Verifique sua conexão com internet
Vídeos grandes naturalmente levam mais tempo
O sistema mostra progresso em tempo real
Comandos de Administrador
!auth_youtube    # Reautentica com YouTube
!limpar_fila     # Limpa toda a fila de uploads
🔒 Segurança
Permissões Necessárias
Discord Bot: Apenas no canal especificado (CANAL_DISCORD_ID)
YouTube API: Apenas permissão de upload de vídeos
Sistema Local: Apenas leitura/escrita na pasta videos/
Proteções Implementadas
✅ Verificação de canal específico
✅ Timeout automático de interações
✅ Validação de arquivos obrigatórios
✅ Limpeza automática de arquivos sensíveis
📈 Melhores Práticas
Para Metadados Otimizados
Arquivos de Contexto Detalhados: Inclua personagens, locais e eventos importantes
Thumbnails de Qualidade: Use imagens nítidas e atraentes
Nomes de Arquivo Descritivos: Facilite a detecção automática de episódios
Para Performance
Vídeos Otimizados: Use formatos compatíveis (MP4 recomendado)
Conexão Estável: Para uploads grandes, use internet cabeada
Monitoramento: Acompanhe a fila durante uploads em lote
Para Organização
Backup Regular: Mantenha cópia dos vídeos até confirmar o upload
Logs de Atividade: O bot mostra histórico completo no Discord
Agendamento Estratégico: Use horários de pico (12:00) para melhor engajamento
🆘 Suporte
Em caso de problemas:

Verifique os logs no terminal
Confirme que todos os pré-requisitos estão atendidos
Use !status para verificar configurações do sistema
Execute !auth_youtube se houver problemas com o YouTube
📞 Canal de Suporte: Contate o Desenvolvedor.

Documentação atualizada para a versão mais recente do bot 🚀
