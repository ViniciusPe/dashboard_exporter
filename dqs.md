# DQS - Dashboard Quality Score

## Documento de Especificação da Feature

| Campo | Valor |
|-------|-------|
| **Versão** | 1.0.0 (Draft) |
| **Data** | 2026-08-03 |
| **Status** | Em revisão pelo time |
| **KR vinculado** | KR 3.1 - Padrões Corporativos |
| **Ciclo** | Julho/2026 |
| **Responsável** | [Preencher] |

---

## 1. Visão Geral

### 1.1 Objetivo

Estabelecer um modelo padronizado e automatizado de avaliação de qualidade dos dashboards Grafana (Dashboard Quality Score - DQS), permitindo:

- Mensurar objetivamente a qualidade de cada dashboard com base em critérios corporativos
- Fornecer ranking de qualidade para gestão e visibilidade do estado atual da instância
- Oferecer recomendações de melhoria diretamente nos dashboards para os mantenedores
- Acompanhar evolução da qualidade ao longo do tempo

### 1.2 User Story

> Como **responsável pela qualidade de dashboards do Cluster**,
> quero **definir e documentar as dimensões, critérios, regras de cálculo e faixas de score do DQS**,
> para **estabelecer um modelo padronizado e aprovado de avaliação de qualidade dos dashboards, aderente aos padrões corporativos (KR 3.1)**.

### 1.3 Escopo

**Inclui:**
- Engine de avaliação automatizada (Python)
- API REST para consumo dos scores
- Panel HTML injetado em cada dashboard com score e top melhorias
- Dashboard de Ranking DQS para visão gerencial
- Métricas expostas para acompanhamento temporal

**Não inclui (v1):**
- Integração com MCP/LLM para sugestões avançadas por IA
- Gate de qualidade em CI/CD (bloqueio de deploy)
- Plugin nativo Grafana

---

## 2. Arquitetura

### 2.1 Visão Macro

```
┌────────────────────────────────────────────────────────────────────┐
│                       DQS Service (FastAPI)                         │
│                                                                    │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────────────┐ │
│  │ Scheduler  │──>│ DQS Engine   │──>│ Results Store            │ │
│  │ (cron 30m) │   │ (Python)     │   │ (PostgreSQL)             │ │
│  └────────────┘   └──────┬───────┘   └────────────┬─────────────┘ │
│                          │                         │               │
│  ┌───────────────────────▼─────────────────────────▼─────────────┐ │
│  │                      REST API                                  │ │
│  │  GET  /api/v1/scores/ranking      → ranking ordenado           │ │
│  │  GET  /api/v1/scores/summary      → score geral da instância   │ │
│  │  GET  /api/v1/scores/{uid}/details→ detalhes + recomendações   │ │
│  │  POST /api/v1/evaluate            → avalia JSON sob demanda    │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                    │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Prometheus Exporter (:9090/metrics)                            │ │
│  │  dqs_dashboard_score{uid, title, folder}                        │ │
│  │  dqs_dimension_score{uid, dimension}                            │ │
│  │  dqs_instance_avg_score                                         │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                    │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Panel Injector                                                 │ │
│  │  Injeta/atualiza panel HTML com score em cada dashboard         │ │
│  └────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
         │                    │                     │
         ▼                    ▼                     ▼
  ┌─────────────┐   ┌──────────────────┐   ┌─────────────────┐
  │ Grafana API │   │ Prometheus       │   │ Dashboard        │
  │ (busca JSONs│   │ (série temporal  │   │ Ranking DQS      │
  │  + injeta)  │   │  de scores)      │   │ (visão gestão)   │
  └─────────────┘   └──────────────────┘   └─────────────────┘
```

### 2.2 Fluxo de Avaliação

```
1. Scheduler dispara (a cada 30 min, configurável)
2. Service busca lista de todos dashboards via Grafana API
3. Para cada dashboard:
   a. Obtém JSON completo
   b. DQS Engine avalia contra todas as regras configuradas
   c. Gera score final + findings + recomendações de correção
   d. Persiste resultado no banco
   e. Atualiza métricas Prometheus
   f. Atualiza panel HTML no dashboard avaliado
4. Calcula score médio da instância
```

---

## 3. Panel de Score nos Dashboards

### 3.1 Formato

Panel do tipo `text` com `mode: html`, posicionado no **topo** de cada dashboard avaliado. Full width, altura mínima (h: 2, ~60px). Não interfere nos painéis existentes.

### 3.2 Layout Visual

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ● 72  │ Dashboard Quality Score: Bom • 72/100                          │
│        │ ⚠ Melhorias: • 17 painéis sem descrição em PT                  │
│        │              • Datasource UID hardcoded em 17 painéis           │
│        │              • Sem links para runbook ou documentação  [Ver →]  │
└─────────────────────────────────────────────────────────────────────────┘
```

- Badge circular com a nota e cor da faixa
- Até 3 melhorias prioritárias (maior severidade primeiro)
- Link para o dashboard de ranking com drilldown no dashboard específico

### 3.3 Cores por faixa

| Faixa | Score | Cor |
|---|---|---|
| Excelente | 90-100 | `#73BF69` (verde) |
| Bom | 70-89 | `#5794F2` (azul) |
| Regular | 50-69 | `#FADE2A` (amarelo) |
| Ruim | 30-49 | `#FF9830` (laranja) |
| Crítico | 0-29 | `#F2495C` (vermelho) |

### 3.4 Origem das recomendações

As recomendações são **determinísticas** — geradas pela engine Python com base nas regras que falharam. Cada regra possui um texto fixo de ação mapeado no YAML de configuração:

```yaml
recommendations:
  panel_descriptions_in_portuguese:
    text: "Adicione descrição em português nos painéis ({count} sem descrição)"
    action: "Edite cada painel > Panel options > Description"
  
  no_hardcoded_datasource_uid:
    text: "Datasource com UID hardcoded ({count} painéis)"
    action: "Crie variável datasource em Settings > Variables > tipo Datasource"
  
  dashboard_title_convention:
    text: "Nome não segue padrão 'Área - Tecnologia - Objetivo'"
    action: "Renomeie em Settings > General > Title"
```

### 3.5 Comportamento

- Adicionado automaticamente na primeira avaliação
- Atualizado a cada ciclo do scheduler
- Identificado por campo `"dqs_managed": true` no JSON do panel
- Se deletado pelo usuário, será re-injetado no próximo ciclo
- Painéis existentes são deslocados para acomodar (gridPos.y += 2)

---

## 4. Dashboard de Ranking DQS

### 4.1 Objetivo

Dashboard dedicado para gestão visualizar a qualidade geral e o ranking de todos os dashboards da instância.

### 4.2 Painéis

| Painel | Tipo | Descrição |
|---|---|---|
| Score Geral da Instância | Stat | Média ponderada de todos os dashboards |
| Total Avaliados | Stat | Quantidade de dashboards sob avaliação |
| Abaixo do Threshold | Stat | Dashboards com score < 70 (atenção) |
| Evolução do Score | Timeseries | Score médio ao longo do tempo |
| Ranking Completo | Table | Título, Pasta, Score, Top Issues, Última Avaliação |
| Score por Dimensão | Bar Gauge | Média de cada dimensão na instância |
| Top Problemas | Table | Regras mais violadas entre todos dashboards |
| Distribuição por Faixa | Pie Chart | % dashboards em cada faixa |

### 4.3 Drilldown

Clicar em um dashboard na tabela de ranking abre visão detalhada com:
- Score por dimensão (bar gauge ou radar)
- Lista completa de regras (passou/falhou com recomendação)
- Histórico de evolução do score

---

## 5. Modelo de Avaliação - Dimensões e Regras

### 5.1 Resumo das Dimensões

| # | Dimensão | Peso | Foco |
|---|---|---|---|
| 1 | Padronização | 25% | Nomenclatura, idioma, tags, convenções |
| 2 | Instrumentação | 25% | Qualidade das queries e métricas |
| 3 | Visualização | 20% | Layout, organização, boas práticas visuais |
| 4 | Ownership | 15% | Responsável, documentação, links |
| 5 | Alertas | 5% | Presença de alertas (opcional/bonificação) |
| 6 | Performance | 10% | Otimização, quantidade de painéis, time range |

**Total: 100%**

### 5.2 Fórmula de Cálculo

```
Score_dimensão = Σ(score_regra × peso_regra) / Σ(peso_regra)

Score_final = Σ(score_dimensão × peso_dimensão) / 100

Score_instância = Σ(score_final de cada dashboard) / total_dashboards
```

Todas as regras são avaliadas de 0 a 100. Score 100 = compliance total, 0 = violação total.

---

### 5.3 Dimensão 1: Padronização (peso: 25%)

Avalia aderência às convenções de nomenclatura, idioma e identificação.

| Regra | Peso | Severidade | Descrição |
|---|---|---|---|
| **Título do dashboard** | 30% | Alta | Deve seguir o padrão: `Área - Tecnologia - Objetivo`. Ex: `NoSQL - MongoDB - Overview Metrics` |
| **Títulos dos painéis em inglês** | 25% | Alta | Todos os títulos de painéis devem estar em inglês. Palavras em português nos títulos penalizam o score |
| **Descrição dos painéis em PT-BR** | 25% | Alta | Todo painel deve possuir campo `description` preenchido em português, explicando o que é exibido e sua relevância |
| **Tags (mínimo 3)** | 15% | Média | Dashboard deve ter no mínimo 3 tags com relação direta ao conteúdo. Ex: `mongodb`, `nosql`, `database` |
| **UID padronizado** | 5% | Baixa | UID deve ser descritivo e em kebab-case. Ex: `nosql-mongodb-overview` |

**Exemplos de violação:**
- Título "Dashboard MongoDB" → falta padrão Área - Tecnologia - Objetivo
- Painel "Mensagens de Entrada" → título em português
- Painel sem description → penaliza proporcionalmente
- Dashboard com 1 tag → abaixo do mínimo

---

### 5.4 Dimensão 2: Instrumentação (peso: 25%)

Avalia a qualidade técnica das queries PromQL/LogQL e uso correto de recursos do Grafana.

| Regra | Peso | Severidade | Descrição |
|---|---|---|---|
| **Uso de `$__rate_interval`** | 20% | Alta | Queries com `rate()`, `irate()` ou `increase()` devem usar `[$__rate_interval]` e nunca intervalos fixos como `[5m]` |
| **Uso de variáveis de template** | 20% | Alta | Queries devem utilizar variáveis de template (`$var`) para filtros, permitindo interatividade e reuso |
| **Sem métricas soltas** | 20% | Alta | Toda query deve usar pelo menos uma função de agregação ou transformação (`sum`, `avg`, `rate`, `max`, `histogram_quantile`, etc.). Métrica bruta exposta sem função é penalizada |
| **legendFormat definido** | 15% | Média | Toda query deve ter `legendFormat` preenchido com labels relevantes. Legenda vazia ou `__auto` penaliza |
| **Datasource sem UID hardcoded** | 15% | Média | O datasource deve usar variável (`$datasource`) ou provisioning. UID hardcoded dificulta portabilidade entre ambientes |
| **Sem queries duplicadas** | 10% | Baixa | Não devem existir queries idênticas (mesma `expr`) em painéis diferentes do mesmo dashboard |

**Exemplos de violação:**
- `rate(metric[5m])` → deveria ser `rate(metric[$__rate_interval])`
- `node_cpu_seconds_total` sozinha sem `rate()` ou `avg()` → métrica solta
- `legendFormat: ""` → sem formato de legenda
- Mesma query `sum(http_requests_total)` em 2 painéis distintos → duplicação

---

### 5.5 Dimensão 3: Visualização (peso: 20%)

Avalia organização visual, layout e boas práticas de UX nos dashboards.

| Regra | Peso | Severidade | Descrição |
|---|---|---|---|
| **Organização por rows** | 25% | Alta | O dashboard deve estar separado por rows (seções). Nomes das rows em inglês. Proporção mínima: 1 row a cada 4-6 painéis |
| **Painéis solitários ocupam largura total** | 20% | Média | Se um painel está sozinho em sua linha (sem painel ao lado), deve ter `w: 24` (largura total). Painel estreito sozinho é desperdício de espaço |
| **Unidades definidas** | 20% | Alta | Painéis numéricos (timeseries, stat, gauge) devem ter unidade configurada (`ms`, `bytes`, `percent`, `short`, etc.) |
| **Legendas com cálculos** | 15% | Média | Painéis timeseries devem ter legenda com calcs relevantes (ex: `mean`, `max`, `last`). Legenda sem calcs oferece pouca informação |
| **Tooltip multi-series** | 10% | Baixa | Painéis timeseries devem usar tooltip `mode: multi` para comparação visual entre séries |
| **Sem sobreposição de painéis** | 10% | Média | Painéis não devem se sobrepor no grid (gridPos conflitante) |

**Observação sobre seção Overview:**
É recomendado (não obrigatório) que dashboards possuam uma seção "Overview" no topo com métricas-resumo em stat panels. Presença de uma row "Overview" é bonificação (+5 pontos na dimensão), não penalização por ausência.

**Exemplos de violação:**
- Dashboard com 12 painéis e nenhuma row → tudo solto
- Painel com `w: 12` sozinho na linha (nenhum outro no mesmo `y`) → deveria ser `w: 24`
- Painel timeseries mostrando bytes mas `unit: "short"` → unidade incorreta
- Row com título "Erros por Serviço" → deveria ser em inglês

---

### 5.6 Dimensão 4: Ownership (peso: 15%)

Avalia se o dashboard possui identificação de responsável, documentação associada e rastreabilidade.

| Regra | Peso | Severidade | Descrição |
|---|---|---|---|
| **Descrição do dashboard preenchida** | 30% | Alta | O campo `description` do dashboard (Settings > General) deve estar preenchido com propósito e contexto |
| **Informação de mantenedor/área** | 30% | Alta | Deve haver identificação do time ou pessoa responsável. Verificado em: description do dashboard, annotations ou links. Padrões aceitos: `@team`, `squad:`, `owner:`, `mantido por:` |
| **Links para documentação** | 25% | Média | Dashboard deve ter pelo menos 1 link (Dashboard Settings > Links) apontando para runbook, wiki, Confluence ou repositório |
| **Organização em pasta** | 15% | Baixa | Dashboard deve estar em uma pasta nomeada (não em "General"). A pasta agrupa dashboards por área/tecnologia |

**Onde buscar informação de mantenedor:**
1. Campo `description` do dashboard (Settings > General > Description)
2. Links do dashboard (Settings > Links) — ex: link para página do time
3. Annotations customizadas
4. Tags que identifiquem o time (ex: tag `team-middleware`)

**Exemplos de compliance:**
- Description: "Dashboard de monitoramento do IIB. Mantido por: squad-middleware. Confluence: [link]"
- Link: `{ title: "Runbook", url: "https://confluence.../runbook-iib" }`

---

### 5.7 Dimensão 5: Alertas (peso: 5%)

Avaliação **opcional e bonificadora**. A ausência de alertas não penaliza severamente — muitos dashboards são de visualização pura. Porém, a presença de alertas é indicador de maturidade operacional.

| Regra | Peso | Severidade | Descrição |
|---|---|---|---|
| **Presença de alertas** | 60% | Baixa | Dashboard com pelo menos 1 alerta configurado recebe score cheio. Sem alertas: score 50 (não zero) |
| **Annotations habilitados** | 40% | Baixa | Ter annotations habilitados para correlacionar deploys, incidentes, etc. com as métricas |

**Nota:** Como alertas são opcionais, o peso desta dimensão é intencionalmente baixo (5%). Um dashboard sem alertas perde no máximo 2.5 pontos no score final.

---

### 5.8 Dimensão 6: Performance (peso: 10%)

Avalia se o dashboard é otimizado para carregamento e uso responsável de recursos.

| Regra | Peso | Severidade | Descrição |
|---|---|---|---|
| **Quantidade de painéis** | 25% | Média | Dashboard com mais de 25 painéis (excluindo rows) compromete performance de renderização. Ideal: até 20 |
| **Time range padrão razoável** | 20% | Média | Time range default deve ser ≤ 6h. Dashboards com `now-24h` ou maior por padrão geram queries pesadas |
| **Sem queries sem filtro** | 25% | Alta | Queries sem nenhum label selector (`metric_name` puro sem `{}`) fazem full scan no Prometheus. Toda query deve ter pelo menos 1 filtro de label |
| **graphTooltip compartilhado** | 10% | Baixa | `graphTooltip` deve ser `1` (shared crosshair) ou `2` (shared tooltip) para UX consistente |
| **Intervalo de refresh adequado** | 20% | Média | Auto-refresh não deve ser menor que 30s para evitar sobrecarga. Ideal: 1m ou 5m |

**Exemplos de violação:**
- Dashboard com 35 painéis → acima do limite recomendado
- `time.from: "now-7d"` como padrão → range muito amplo para carga inicial
- Query `up` sem label filter → full scan em todos os targets
- Auto-refresh de `5s` → sobrecarga desnecessária

---

## 6. Faixas de Aderência

| Faixa | Score | Significado | Ação esperada |
|---|---|---|---|
| **Excelente** | 90 – 100 | Aderente a todos os padrões | Manutenção contínua |
| **Bom** | 70 – 89 | Maioria dos padrões atendidos | Melhorias pontuais opcionais |
| **Regular** | 50 – 69 | Gaps relevantes identificados | Plano de ação recomendado |
| **Ruim** | 30 – 49 | Múltiplas violações críticas | Plano de ação obrigatório |
| **Crítico** | 0 – 29 | Não aderente aos padrões | Ação imediata requerida |

### Threshold corporativo

O **score mínimo aceitável** para dashboards será definido pelo time. Sugestão: **70 (Bom)**.

Dashboards abaixo deste threshold aparecerão em destaque no ranking como "atenção necessária".

---

## 7. API REST - Contratos Principais

### `GET /api/v1/scores/ranking`

```json
{
  "instance_score": 68.5,
  "instance_label": "Regular",
  "total_dashboards": 47,
  "below_threshold": 12,
  "evaluated_at": "2026-08-03T14:30:00Z",
  "ranking": [
    {
      "uid": "nosql-mongodb-overview",
      "title": "NoSQL - MongoDB - Overview Metrics",
      "folder": "NoSQL",
      "score": 92.1,
      "label": "Excelente",
      "top_issues": [],
      "evaluated_at": "2026-08-03T14:28:00Z"
    },
    {
      "uid": "iib-otel-dashboard",
      "title": "IBM IIB - Integration Bus Monitoring",
      "folder": "Middleware",
      "score": 72.3,
      "label": "Bom",
      "top_issues": ["17 painéis sem descrição em PT", "Datasource UID hardcoded"],
      "evaluated_at": "2026-08-03T14:28:00Z"
    }
  ]
}
```

### `GET /api/v1/scores/{uid}/details`

```json
{
  "uid": "iib-otel-dashboard",
  "title": "IBM IIB - Integration Bus Monitoring",
  "score": 72.3,
  "label": "Bom",
  "dimensions": [
    {
      "id": "standardization",
      "label": "Padronização",
      "score": 55.0,
      "weight": 25,
      "rules": [
        {
          "id": "dashboard_title_convention",
          "passed": true,
          "score": 100,
          "recommendation": null
        },
        {
          "id": "panel_descriptions_in_portuguese",
          "passed": false,
          "score": 0,
          "recommendation": "Adicione descrição em PT-BR nos 17 painéis sem descrição"
        }
      ]
    }
  ],
  "history": [
    {"date": "2026-08-03", "score": 72.3},
    {"date": "2026-08-02", "score": 70.1}
  ]
}
```

### `POST /api/v1/evaluate`

Avalia um JSON de dashboard sob demanda (sem persistir). Útil para testar antes de publicar.

---

## 8. Métricas Prometheus Expostas

```
# Score individual por dashboard
dqs_dashboard_score{uid="iib-otel-dashboard", title="IBM IIB", folder="Middleware"} 72.3

# Score por dimensão
dqs_dimension_score{uid="iib-otel-dashboard", dimension="standardization"} 55.0
dqs_dimension_score{uid="iib-otel-dashboard", dimension="instrumentation"} 88.0

# Métricas da instância
dqs_instance_avg_score 68.5
dqs_dashboards_total 47
dqs_dashboards_below_threshold{threshold="70"} 12

# Métricas operacionais
dqs_evaluation_duration_seconds 4.2
dqs_last_evaluation_timestamp 1722695400
```

---

## 9. Premissas

1. A instância Grafana possui API acessível pelo DQS Service
2. Será criado um Service Account com permissão de leitura + escrita em dashboards
3. Os critérios definidos neste documento são aprovados pelo time antes da implementação
4. O panel de score é aceito como padrão visual em todos os dashboards
5. O DQS **apenas reporta** — não bloqueia criação ou edição de dashboards
6. A avaliação é **estática** (estrutura do JSON) — não avalia se os dados exibidos estão corretos
7. Regras são configuráveis via YAML e ajustáveis sem necessidade de redeploy

---

## 10. Riscos e Dependências

### Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Divergência no entendimento dos critérios entre os times | Alto | Documento revisado e aprovado formalmente |
| Rejeição do panel injetado pelos donos de dashboards | Médio | Oferecer flag de opt-out via tag `dqs:skip` |
| Mudança nos padrões corporativos após implementação | Baixo | Regras em YAML permitem ajuste sem redeploy |
| Avaliação incorreta de idioma (falsos positivos) | Médio | Lista curada de termos + revisão periódica |

### Dependências

| Dependência | Descrição | Status |
|---|---|---|
| Alinhamento com áreas donas dos padrões | Validação das regras e pesos | Pendente |
| Service Account no Grafana | Token com permissão Admin/Editor | A solicitar |
| Infraestrutura | Container runtime + PostgreSQL | A verificar |
| Prometheus | Target de scrape para métricas DQS | A configurar |

---

## 11. Critérios de Aceite

- [ ] Dimensões e critérios do DQS definidos, documentados e aprovados
- [ ] Regras de cálculo do score determinísticas e reproduzíveis
- [ ] Faixas de aderência definidas e documentadas
- [ ] Modelo revisado e aprovado pelos responsáveis
- [ ] Panel de score funcionando e visível nos dashboards
- [ ] Dashboard de Ranking funcional com score geral e tabela
- [ ] API REST respondendo com scores e detalhes
- [ ] Métricas sendo exportadas para Prometheus
- [ ] Documentação em local compartilhado e acessível

---

## 12. Roadmap

### v1.0 (Escopo atual)
- Engine Python com regras em YAML
- API REST (FastAPI)
- Panel de score injetado nos dashboards
- Dashboard de Ranking
- Métricas Prometheus
- Histórico em PostgreSQL

### v1.1 (Evolução próxima)
- Opt-out por dashboard (tag `dqs:skip`)
- Relatório periódico (PDF/email para gestão)
- Avaliação sob demanda via CLI
- Webhook para integração com CI/CD

### v2.0 (Futuro)
- Camada MCP/LLM para sugestões contextuais avançadas
- Gate de qualidade em pipeline (bloqueia merge se score < threshold)
- Multi-instância (avaliar múltiplos Grafana)
- Plugin nativo Grafana

---

## 13. Pontos em aberto para discussão do time

1. **Pesos das dimensões** — Os pesos sugeridos estão adequados? Instrumentação e Padronização devem ter o mesmo peso (25%)?
2. **Threshold mínimo** — Score 70 como mínimo aceitável é viável para o estado atual dos dashboards?
3. **Panel injetado** — O time aceita que o DQS injete automaticamente um panel em todos os dashboards? Ou preferem apenas o dashboard de ranking?
4. **Alertas opcionais** — Concordam que alertas não devem penalizar fortemente? (peso 5%)
5. **Tags mínimas** — 3 tags é um bom mínimo? Devem existir tags obrigatórias (ex: nome do time)?
6. **Identificação de ownership** — Qual o melhor local para registrar o mantenedor? Description? Tag? Link?
7. **Frequência de avaliação** — 30 minutos é adequado ou pode ser menos frequente?
8. **Opt-out** — Deve existir mecanismo para excluir dashboards temporários/experimentais da avaliação?
9. **Painéis solitários** — A regra de "painel sozinho deve ocupar largura total" se aplica a stat panels pequenos na row Overview?

---

*Documento sujeito a revisão. Aguardando feedback do time para consolidação das regras e aprovação formal.md
