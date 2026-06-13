# Codabench Competition Bundle Upload Script

This document describes the upload utility, which transfers a Codabench competition bundle ZIP file to a Codabench instance through the Codabench REST API. We provide this utility so that a validated bundle can be published without manual interaction with the Codabench web interface.

The script performs the following steps automatically:

1. Authenticate with Codabench
2. Create a competition bundle dataset
3. Upload the ZIP file
4. Finalize the upload
5. Wait for the competition to be created

## Requirements

The utility depends on one third-party package, which can be installed as follows:

```bash
pip install python-dotenv
```

Configuration is supplied through a `.env` file with the following contents:

```env
CODABENCH_BASE_URL=https://www.codabench.org
CODABENCH_TOKEN=your_token_here  # optional; omit to authenticate with --username/--password
```

The `CODABENCH_TOKEN` entry is optional; when it is omitted, the script obtains a token from the username and password supplied on the command line.

## Usage

The following invocation authenticates with an API token read from `.env`:

```bash
python -m autocodabench.upload.codabench_api competition_bundle.zip
```

Alternatively, the script accepts a username and password, from which it obtains a token at runtime:

```bash
python -m autocodabench.upload.codabench_api \
  --username USERNAME \
  --password PASSWORD \
  competition_bundle.zip
```

A self-hosted or otherwise non-default Codabench instance can be targeted by overriding the base URL:

```bash
python -m autocodabench.upload.codabench_api \
  --base-url https://your-codabench-instance.org \
  competition_bundle.zip
```
