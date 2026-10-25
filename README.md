# EAJ Cafés Especiais — Sistema completo

Loja virtual, painel administrativo e painel de análise de dados em uma única aplicação,
ligados ao mesmo banco de dados.

| Área | Endereço | Quem acessa |
|---|---|---|
| Loja (com a experiência cinematográfica) | `/` | Público |
| Consulta de pedido | `/pedido/consultar` | Cliente (número do pedido + e-mail) |
| Minha conta | `/conta/pedidos` | Cliente cadastrado |
| Painel administrativo | `/admin/` | Administrador e equipe autorizada |
| Painel de análise (BI) | `/bi/` | Analista e equipe autorizada |

Tecnologia: Python 3.10+ com Flask, SQLite (um arquivo em `instance/eaj.sqlite3`), HTML, CSS e JavaScript
sem frameworks. Os gráficos são SVG desenhados pelo próprio sistema (`eaj/static/js/charts.js`), sem bibliotecas externas.
As únicas dependências são Flask, python-dotenv e Pillow.

---

## 1. Instalação

```bash
pip install -r requirements.txt
python manage.py setup
python manage.py create-admin
python manage.py run
```

- `setup` cria o arquivo `.env` com uma chave secreta aleatória, o banco, os dados iniciais e prepara os frames.
  Pode ser executado de novo sem apagar nada.
- `create-admin` pede e-mail, nome e senha. A senha é digitada no terminal e **não fica gravada no código nem no `.env`**.
  Para criar um analista: `python manage.py create-admin --role analista`.
  Em instalação automatizada, a senha pode vir da variável de ambiente `EAJ_ADMIN_PASSWORD`.
- `run` abre o servidor de desenvolvimento em http://127.0.0.1:5000, com recarga automática do navegador.

Testes: `python manage.py test`

### Produção
Use um servidor WSGI (por exemplo `waitress-serve --call eaj:create_app` no Windows ou `gunicorn "eaj:create_app()"` no Linux),
HTTPS e `SESSION_COOKIE_SECURE=1` no `.env`. Faça backup do arquivo `instance/eaj.sqlite3` regularmente.
Para mais de um servidor ou muitos acessos simultâneos, o banco pode migrar para PostgreSQL: o acesso a dados está
concentrado em `eaj/db.py` e nos módulos de `eaj/services/`.

---

## 2. Dados iniciais

Somente o que foi definido pela empresa:

- **EAJ Café Especial | 500 g** — R$ 30,00
- **EAJ Café Especial | 1 kg** — R$ 60,00
- Sócios: **Família 50%** e **Parceiro investidor 50%**
- Forma de entrega: **"Entrega a combinar"** (sem valor inventado)
- Categorias financeiras (só a estrutura, sem lançamentos)

O **custo unitário** e o **estoque** começam vazios. Antes de vender:
1. *Produtos e preços*: cadastre o custo unitário de cada produto (necessário para CPV e margem).
2. *Estoque*: registre a entrada das unidades disponíveis. Sem estoque, o produto aparece como esgotado na loja.
3. *Formas de entrega*: cadastre os valores reais de frete, se houver.
4. *Loja e contato*: preencha WhatsApp, e-mail e instruções de pagamento mostradas ao cliente.

---

## 3. Regras de negócio

### Pedido, pagamento e estoque
| Momento | Status | Estoque |
|---|---|---|
| Cliente finaliza o pedido | Aguardando pagamento | **Reserva** (sai do disponível, continua no físico) |
| Admin registra o pagamento que cobre o total | Pago | **Baixa definitiva** |
| Em preparo → Enviado → Entregue | — | — |
| Cancelado antes do pagamento | Cancelado | Reserva **liberada** |
| Cancelado depois do pagamento | Cancelado | Baixa **estornada** (exige confirmar o reembolso) |

- Disponível = estoque físico − reservado. Não é possível vender acima do disponível (verificação dentro de transação).
- Cada movimentação ligada a um pedido só pode existir uma vez (índice único no banco), então repetir uma ação não duplica a baixa.
- **Nenhum pedido vira "pago" sozinho.** Sem integração de pagamento, o cliente vê que o pedido foi registrado
  e que o pagamento será combinado. O pedido só é confirmado quando alguém registra o pagamento recebido.
- Com frete "a combinar", o valor do frete é definido na tela do pedido antes de registrar o pagamento.
- Preço e custo de cada item ficam gravados no pedido. Mudar o preço depois não altera pedidos antigos.
- O navegador envia só produto e quantidade; preços e totais são sempre recalculados no servidor.

### Indicadores financeiros
- **Venda** = pedido com pagamento confirmado, na data da confirmação.
- **Receita bruta** = total dos pedidos vendidos (produtos + frete cobrado).
- **Receita líquida** = receita bruta − impostos lançados.
- **CPV** = quantidade × custo unitário gravado no pedido.
- **Lucro bruto** = receita líquida − CPV.
- **Resultado operacional** = lucro bruto − despesas fixas, variáveis e custos operacionais.
- **Resultado líquido estimado** = resultado operacional + outras receitas.
- **Compras de mercadoria** (café verde, embalagens) aparecem no caixa e no BI, mas ficam **fora do resultado**,
  porque o custo entra pelo CPV quando o produto é vendido. Somar os dois seria contar o mesmo gasto duas vezes.
- Quando falta algum dado (custo não cadastrado, nenhum imposto lançado), o indicador aparece marcado como **estimativa**.
- Valores em dinheiro são guardados em centavos inteiros; nenhum cálculo financeiro usa ponto flutuante.

Esta é uma visão gerencial e não substitui a apuração contábil e tributária da empresa.

### Distribuição entre sócios
A participação de 50% / 50% se aplica ao **valor aprovado para distribuição**, nunca à receita bruta:

resultado líquido estimado → (−) reserva de caixa → valor disponível → base aprovada → divisão entre os sócios

- O percentual de reserva e a base aprovada são definidos na tela *Sócios e distribuição*.
- Só é possível aprovar com resultado positivo, base dentro do disponível e sem outra distribuição aprovada no mesmo período.
- O sistema **registra** a decisão (valores, datas e responsável); **não faz transferências bancárias**.

---

## 4. Como usar o painel

- **Cadastrar ou alterar produtos e preços:** *Produtos e preços* → *Novo produto* ou clique no produto.
  Toda alteração de preço ou custo entra no histórico do produto.
- **Registrar despesas, compras e impostos:** *Lançamentos* → *Novo lançamento*. Escolha a categoria (o tipo dela define
  como entra no resultado), a data de competência e se já foi pago (realizado) ou está previsto.
- **Movimentar estoque:** *Estoque* → registrar entrada, ajuste ou perda (ajustes e perdas exigem motivo).
- **Confirmar um pedido:** *Pedidos* → abrir o pedido → definir o frete, se estiver "a combinar" → *Registrar pagamento*.
- **Usuários e permissões:** administradores têm acesso total. Para analistas e equipe, marque as áreas permitidas
  (pedidos, produtos, estoque, financeiro, sócios, entrega, BI, usuários, histórico).
- **Histórico de alterações:** toda mudança administrativa e financeira fica registrada com usuário, data e detalhes.

### Painel de análise (BI)
Filtros por período, agrupamento (dia, semana, mês, ano), produto, status do pedido e categoria de despesa.
Todos os indicadores e gráficos respondem aos mesmos filtros. O painel verifica a cada 20 segundos se houve novos
pedidos, pagamentos, lançamentos ou movimentações e se atualiza sozinho. Os relatórios podem ser exportados em CSV.
Sem dados, os gráficos mostram estados vazios, nunca números fictícios.

---

## 5. Experiência cinematográfica (frames)

A página inicial usa a pasta `FRAMES VIDEO/` (configurável em `FRAMES_SOURCE` no `.env`).
Hoje ela contém o vídeo `video-cafe.mp4` (8 s, 24 quadros por segundo, 1280×720), de onde saem os **192 quadros**.

**Para trocar a sequência:**
1. Coloque na pasta `FRAMES VIDEO/` **o vídeo** (MP4, MOV ou WEBM) — o mais recente é usado — ou, sem vídeo,
   as imagens numeradas (`frame_001`, `frame_002`, …).
2. Rode `python manage.py prepare-frames`.
   Para extrair de vídeo é preciso o ffmpeg: `pip install -r requirements-dev.txt` (ou o ffmpeg instalado no sistema).
   Opcional: `--fps 12` usa metade dos quadros (arquivos menores, movimento um pouco menos liso).
3. Isso gera em `eaj/static/frames/`:
   - `lg/` — resolução nativa (computadores);
   - `md/` — 960 px (telas menores e conexões lentas);
   - `pt/` — recorte vertical em resolução nativa (celular em pé);
   - `manifest.json`, e a foto do produto `eaj/static/img/produto-eaj.jpg` (recorte do último quadro).
4. Os textos de cada etapa ficam no início de `eaj/templates/shop/index.html` (lista `chapters`):
   `from` é o ponto da rolagem (0 a 1) em que o texto aparece; `at` é o ponto do vídeo usado na versão estática.

**Qualidade:** use o vídeo, não alguns quadros soltos. Poucos quadros viram um slideshow, e quanto maior a resolução
do vídeo, mais nítido fica em telas grandes (o atual é 720p; um vídeo 1080p rende visivelmente melhor em monitores).

Como funciona: a seção é alta, o palco fica preso com `position: sticky`, e a posição da rolagem vira o índice do
quadro. Com a sequência completa, cada posição mostra um quadro real, como um vídeo; com menos de 60 quadros,
o canvas funde vizinhos para suavizar. Cada aparelho baixa só uma versão. O carregamento é progressivo
(amostra espalhada primeiro, depois os intervalos, 6 downloads em paralelo, priorizando a posição atual).
Com `prefers-reduced-motion` ou sem JavaScript, a seção vira uma sequência estática de imagens e textos.

---

## 6. Integração de pagamento (futura)

Hoje não há pagamento online. Para integrar um provedor (Pix, cartão):
1. Defina `PAYMENT_PROVIDER` no `.env`. A loja passa a mostrar a mensagem de pagamento online.
2. Crie uma rota de webhook que valide a assinatura do provedor e chame `orders.register_payment(...)`
   com o valor confirmado. Essa função já aplica todas as regras (baixa de estoque, status, histórico).
3. Nunca confirme pagamento a partir do retorno do navegador do cliente: só pela notificação validada do provedor.

---

## 7. Estrutura

```
manage.py                 comandos (setup, create-admin, prepare-frames, run, test)
eaj/__init__.py           criação da aplicação, filtros, erros
eaj/config.py             configuração lida do .env
eaj/db.py                 conexão SQLite, transações, migrations
eaj/migrations/           esquema do banco (SQL versionado)
eaj/money.py              dinheiro em centavos e percentuais
eaj/security.py           login, perfis, permissões, CSRF
eaj/seed.py               dados iniciais oficiais
eaj/services/             regras de negócio (pedidos, estoque, catálogo, financeiro, sócios, análises, auditoria)
eaj/blueprints/           rotas: shop (loja), account (conta), admin (painel), bi (análise)
eaj/templates/            páginas
eaj/static/               CSS, JavaScript, imagens e frames processados
tests/                    testes automatizados
FRAMES VIDEO/             frames originais da experiência
```
