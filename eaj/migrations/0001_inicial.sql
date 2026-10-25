-- EAJ Cafés Especiais — esquema inicial
-- Valores monetários em centavos (INTEGER). Percentuais em pontos-base (5000 = 50,00%).
-- Datas e horários no formato ISO 'AAAA-MM-DD HH:MM:SS', horário local do servidor.

CREATE TABLE users (
  id            INTEGER PRIMARY KEY,
  email         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  name          TEXT    NOT NULL,
  password_hash TEXT    NOT NULL,
  role          TEXT    NOT NULL CHECK (role IN ('admin','analista','cliente')),
  permissions   TEXT    NOT NULL DEFAULT '',          -- lista separada por vírgula (ver security.PERMISSIONS)
  active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  created_at    TEXT    NOT NULL,
  last_login_at TEXT
);

CREATE TABLE customers (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER UNIQUE REFERENCES users(id) ON DELETE SET NULL,
  name       TEXT NOT NULL,
  email      TEXT NOT NULL COLLATE NOCASE,
  phone      TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_customers_email ON customers(email);

CREATE TABLE products (
  id                  INTEGER PRIMARY KEY,
  slug                TEXT    NOT NULL UNIQUE,
  name                TEXT    NOT NULL,
  weight_grams        INTEGER NOT NULL CHECK (weight_grams > 0),
  description         TEXT    NOT NULL DEFAULT '',
  price_cents         INTEGER NOT NULL CHECK (price_cents >= 0),
  unit_cost_cents     INTEGER CHECK (unit_cost_cents IS NULL OR unit_cost_cents >= 0),  -- NULL = custo não informado
  stock_on_hand       INTEGER NOT NULL DEFAULT 0 CHECK (stock_on_hand >= 0),
  stock_reserved      INTEGER NOT NULL DEFAULT 0 CHECK (stock_reserved >= 0),
  low_stock_threshold INTEGER NOT NULL DEFAULT 0 CHECK (low_stock_threshold >= 0),
  image               TEXT    NOT NULL DEFAULT '',
  active              INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  sort_order          INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT    NOT NULL,
  updated_at          TEXT    NOT NULL,
  CHECK (stock_reserved <= stock_on_hand)
);

CREATE TABLE price_history (
  id              INTEGER PRIMARY KEY,
  product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  price_cents     INTEGER NOT NULL,
  unit_cost_cents INTEGER,
  changed_by      INTEGER REFERENCES users(id),
  changed_at      TEXT    NOT NULL,
  note            TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX ix_price_history_product ON price_history(product_id, changed_at);

CREATE TABLE shipping_methods (
  id          INTEGER PRIMARY KEY,
  name        TEXT    NOT NULL,
  description TEXT    NOT NULL DEFAULT '',
  price_cents INTEGER CHECK (price_cents IS NULL OR price_cents >= 0),   -- NULL = valor a combinar
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE orders (
  id                 INTEGER PRIMARY KEY,
  code               TEXT    NOT NULL UNIQUE,
  public_token       TEXT    NOT NULL,
  customer_id        INTEGER NOT NULL REFERENCES customers(id),
  status             TEXT    NOT NULL CHECK (status IN ('aguardando_pagamento','pago','em_preparo','enviado','entregue','cancelado')),
  subtotal_cents     INTEGER NOT NULL CHECK (subtotal_cents >= 0),
  shipping_cents     INTEGER NOT NULL DEFAULT 0 CHECK (shipping_cents >= 0),
  total_cents        INTEGER NOT NULL CHECK (total_cents >= 0),
  shipping_method_id INTEGER REFERENCES shipping_methods(id),
  shipping_label     TEXT    NOT NULL,
  shipping_to_arrange INTEGER NOT NULL DEFAULT 0 CHECK (shipping_to_arrange IN (0,1)),
  customer_name      TEXT NOT NULL,
  customer_email     TEXT NOT NULL,
  customer_phone     TEXT NOT NULL,
  ship_zip           TEXT NOT NULL DEFAULT '',
  ship_street        TEXT NOT NULL DEFAULT '',
  ship_number        TEXT NOT NULL DEFAULT '',
  ship_complement    TEXT NOT NULL DEFAULT '',
  ship_district      TEXT NOT NULL DEFAULT '',
  ship_city          TEXT NOT NULL DEFAULT '',
  ship_state         TEXT NOT NULL DEFAULT '',
  notes              TEXT NOT NULL DEFAULT '',
  created_at         TEXT NOT NULL,
  paid_at            TEXT,
  shipped_at         TEXT,
  delivered_at       TEXT,
  cancelled_at       TEXT,
  cancel_reason      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX ix_orders_status ON orders(status);
CREATE INDEX ix_orders_created ON orders(created_at);
CREATE INDEX ix_orders_paid ON orders(paid_at);
CREATE INDEX ix_orders_customer ON orders(customer_id);

CREATE TABLE order_items (
  id               INTEGER PRIMARY KEY,
  order_id         INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id       INTEGER NOT NULL REFERENCES products(id),
  product_name     TEXT    NOT NULL,
  weight_grams     INTEGER NOT NULL,
  quantity         INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
  unit_cost_cents  INTEGER,                 -- custo cadastrado no momento do pedido (NULL = não informado)
  line_total_cents INTEGER NOT NULL CHECK (line_total_cents >= 0),
  UNIQUE (order_id, product_id)
);

CREATE TABLE order_events (
  id          INTEGER PRIMARY KEY,
  order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  from_status TEXT,
  to_status   TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  user_id     INTEGER REFERENCES users(id),
  created_at  TEXT NOT NULL
);
CREATE INDEX ix_order_events_order ON order_events(order_id);

CREATE TABLE payments (
  id           INTEGER PRIMARY KEY,
  order_id     INTEGER NOT NULL REFERENCES orders(id),
  method       TEXT    NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  status       TEXT    NOT NULL CHECK (status IN ('confirmado','estornado')),
  reference    TEXT    NOT NULL DEFAULT '',
  paid_at      TEXT    NOT NULL,
  note         TEXT    NOT NULL DEFAULT '',
  recorded_by  INTEGER REFERENCES users(id),
  created_at   TEXT    NOT NULL
);
CREATE INDEX ix_payments_order ON payments(order_id);

-- Movimentações de estoque.
--   qty_on_hand_delta: efeito no estoque físico
--   qty_reserved_delta: efeito no estoque reservado
-- Tipos ligados a pedido só podem existir uma vez por pedido e produto (evita baixa duplicada).
CREATE TABLE stock_movements (
  id                 INTEGER PRIMARY KEY,
  product_id         INTEGER NOT NULL REFERENCES products(id),
  kind               TEXT    NOT NULL CHECK (kind IN ('entrada','ajuste_entrada','ajuste_saida','perda',
                                                      'reserva','liberacao','baixa_venda','estorno_venda')),
  qty_on_hand_delta  INTEGER NOT NULL,
  qty_reserved_delta INTEGER NOT NULL,
  on_hand_after      INTEGER NOT NULL,
  reserved_after     INTEGER NOT NULL,
  unit_cost_cents    INTEGER,
  order_id           INTEGER REFERENCES orders(id),
  note               TEXT    NOT NULL DEFAULT '',
  user_id            INTEGER REFERENCES users(id),
  created_at         TEXT    NOT NULL
);
CREATE INDEX ix_stock_mov_product ON stock_movements(product_id, created_at);
CREATE UNIQUE INDEX ux_stock_mov_order ON stock_movements(order_id, product_id, kind) WHERE order_id IS NOT NULL;

CREATE TABLE finance_categories (
  id     INTEGER PRIMARY KEY,
  name   TEXT NOT NULL UNIQUE COLLATE NOCASE,
  kind   TEXT NOT NULL CHECK (kind IN ('despesa_fixa','despesa_variavel','compra_mercadoria',
                                       'custo_operacional','imposto','outra_receita')),
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE finance_entries (
  id               INTEGER PRIMARY KEY,
  category_id      INTEGER NOT NULL REFERENCES finance_categories(id),
  description      TEXT    NOT NULL,
  amount_cents     INTEGER NOT NULL CHECK (amount_cents > 0),
  competence_date  TEXT    NOT NULL,              -- AAAA-MM-DD
  due_date         TEXT,                          -- vencimento previsto
  settled_date     TEXT,                          -- data em que foi pago ou recebido
  status           TEXT    NOT NULL CHECK (status IN ('previsto','realizado','cancelado')),
  notes            TEXT    NOT NULL DEFAULT '',
  created_by       INTEGER REFERENCES users(id),
  created_at       TEXT    NOT NULL,
  updated_at       TEXT    NOT NULL,
  CHECK (status <> 'realizado' OR settled_date IS NOT NULL)
);
CREATE INDEX ix_finance_competence ON finance_entries(competence_date);

CREATE TABLE partners (
  id           INTEGER PRIMARY KEY,
  name         TEXT    NOT NULL,
  share_bp     INTEGER NOT NULL CHECK (share_bp BETWEEN 0 AND 10000),
  active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE distributions (
  id                      INTEGER PRIMARY KEY,
  period_start            TEXT    NOT NULL,
  period_end              TEXT    NOT NULL,
  gross_revenue_cents     INTEGER NOT NULL,
  deductions_cents        INTEGER NOT NULL,
  cogs_cents              INTEGER NOT NULL,
  gross_profit_cents      INTEGER NOT NULL,
  operating_expenses_cents INTEGER NOT NULL,
  other_income_cents      INTEGER NOT NULL,
  net_result_cents        INTEGER NOT NULL,
  reserve_bp              INTEGER NOT NULL CHECK (reserve_bp BETWEEN 0 AND 10000),
  reserve_cents           INTEGER NOT NULL CHECK (reserve_cents >= 0),
  approved_base_cents     INTEGER NOT NULL CHECK (approved_base_cents >= 0),
  status                  TEXT    NOT NULL CHECK (status IN ('aprovada','cancelada')),
  notes                   TEXT    NOT NULL DEFAULT '',
  approved_by             INTEGER REFERENCES users(id),
  approved_at             TEXT    NOT NULL,
  cancelled_at            TEXT
);

CREATE TABLE distribution_shares (
  id              INTEGER PRIMARY KEY,
  distribution_id INTEGER NOT NULL REFERENCES distributions(id) ON DELETE CASCADE,
  partner_id      INTEGER NOT NULL REFERENCES partners(id),
  partner_name    TEXT    NOT NULL,
  share_bp        INTEGER NOT NULL,
  amount_cents    INTEGER NOT NULL CHECK (amount_cents >= 0)
);

CREATE TABLE cash_reserves (
  id              INTEGER PRIMARY KEY,
  kind            TEXT    NOT NULL CHECK (kind IN ('constituicao','uso','estorno')),
  amount_cents    INTEGER NOT NULL CHECK (amount_cents > 0),
  distribution_id INTEGER REFERENCES distributions(id),
  note            TEXT    NOT NULL DEFAULT '',
  created_by      INTEGER REFERENCES users(id),
  created_at      TEXT    NOT NULL
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE audit_log (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER REFERENCES users(id),
  action     TEXT NOT NULL,
  entity     TEXT NOT NULL,
  entity_id  INTEGER,
  details    TEXT NOT NULL DEFAULT '{}',
  ip         TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX ix_audit_created ON audit_log(created_at);
