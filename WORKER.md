# Serviço worker no Railway

A explicação que estava dentro do `railway.worker.json` mora aqui. Ela
saiu de lá porque **JSON não tem comentário**: eu tinha inventado uma
chave `_comentario`, o Railway valida contra o schema, e chave
desconhecida derruba o build. Foi uma das duas causas do
*failure to build* de 11/08.

## Configuração

Nas Settings do serviço worker, campo **Config as code** /
**Railway Config File**:

```
railway.worker.json
```

**Sem prefixo `api/`.** A raiz do repositório É a pasta `api` — confira
com `git rev-parse --show-toplevel`. Apontar para `api/railway.worker.json`
faz o Railway procurar um arquivo que não existe, e foi a outra causa do
*failure to build*.

## Por que o serviço worker precisa de config próprio

O `railway.json` da raiz fixa `gunicorn app:app` e vale para **todo**
serviço construído deste repositório. Sem um config próprio, o serviço
worker sobe com esse comando — ou seja, sobe uma **segunda cópia da
API**. Ele passa no healthcheck `/health`, fica **verde no painel**, e
nunca toca na fila.

Foi o que aconteceu em 11/08. "Subiu" estava certo; o que subiu é que não
era um worker. O painel dizendo saudável é o que torna este modo de falha
pior que uma queda.

## Duas escolhas do arquivo, e o motivo de cada uma

**Sem `healthcheckPath`.** O worker não escuta porta nenhuma. Se herdasse
o `/health` do config da raiz, o Railway o mataria em laço por healthcheck
que nunca responde — e o sintoma seria "reiniciando", que leva a um
diagnóstico diferente de "parado".

**`restartPolicyType: ALWAYS`**, não `ON_FAILURE`. Se o worker sair por
qualquer motivo, inclusive com código 0, ninguém está consumindo a fila.
Saída limpa e silenciosa é exatamente o caso que não pode passar.

## Variáveis do serviço (9)

```
DATABASE_URL          ← Add Reference → Postgres (a mesma da API)
ANTHROPIC_API_KEY
OPENAI_API_KEY
PINECONE_API_KEY
PINECONE_INDEX
SENDGRID_API_KEY
EMAIL_FROM_ADDRESS
EMAIL_REPLY_TO
EMAIL_FROM_NAME
```

Não copiar: `PORT` (o worker não escuta), `API_SECRET_KEY`,
`FILA_ASSINCRONA` (essa é só da API).

## Como saber que ele está mesmo consumindo

O painel verde não prova nada — foi assim que a segunda cópia da API
passou por worker. O que prova:

1. `POST /generate-report` com `"assincrono": true` devolve 202 com um id;
2. `GET /status/<id>` sai de `pendente` em poucos segundos.

Se ficar em `pendente`, ninguém está consumindo. A partir de 15 minutos a
própria API alerta o `executivo@` com o código `fila_parada`.
