# Grafana Dashboard Exporter Plugin

Um plugin para o Grafana que permite exportar dashboards para repositórios GitHub.

## Funcionalidades

- **Listar Dashboards**: Visualiza todos os dashboards disponíveis no Grafana
- **Filtros Avançados**: Filtra por nome, tags e pastas
- **Seleção Múltipla**: Seleciona múltiplos dashboards para exportação
- **Exportação para GitHub**: Envia os JSONs dos dashboards para um repositório GitHub
- **Configuração Persistente**: Salva as configurações do GitHub no localStorage

## Como usar

1. **Configurar GitHub**:
   - Clique em "Configurar GitHub"
   - Insira seu token pessoal do GitHub
   - Configure owner/organização, repositório, branch e pasta de destino

2. **Selecionar Dashboards**:
   - Use os filtros para encontrar dashboards específicos
   - Marque os checkboxes dos dashboards desejados
   - Ou use "Selecionar Todos" para marcar todos os filtrados

3. **Exportar**:
   - Clique em "Exportar Selecionados"
   - Os dashboards serão enviados para o repositório GitHub configurado

## Configuração do GitHub Token

1. Acesse GitHub → Settings → Developer settings → Personal access tokens
2. Gere um novo token com permissões de `repo`
3. Use este token na configuração do plugin

## Instalação

1. Compile o plugin: `npm run build`
2. Copie a pasta `dist/` para o diretório de plugins do Grafana
3. Reinicie o Grafana
4. Ative o plugin no Admin → Plugins

## Desenvolvimento

```bash
npm install          # Instalar dependências
npm run dev         # Modo desenvolvimento com watch
npm run build       # Build de produção
npm run lint        # Verificar código
```