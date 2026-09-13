# {{PROJECT_NAME}}

## Prerequisites

- Node.js {{NODE_VERSION}}+
- npm
- Docker (optional, for containerized deployment)

## Setup

```bash
npm install
cp .env.example .env.local
# Fill in your environment variables in .env.local
{{DB_SETUP}}
```

## Development

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Production

```bash
npm run build
npm start
```

## Deploy with Docker

```bash
docker-compose up -d
```

## Environment Variables

See `.env.example` for required variables and `SECRETS_SETUP.md` for CI/CD configuration.
