# Thinkube Control

Thinkube Control is the central management interface for the Thinkube platform, providing service discovery, deployment management, and CI/CD monitoring capabilities.

## Architecture Overview

### Two-Database Design

Thinkube Control uses a **two-database architecture** to separate concerns:

1. **Main Database** (`thinkube_control`)
   - Authentication and authorization data
   - Service registry and health monitoring
   - Deployment templates and logs
   - User preferences and dashboards
   - API tokens

2. **CI/CD Database** (`cicd_monitoring`)
   - Pipeline execution records
   - Build stages and status
   - Performance metrics
   - Workflow tracking

This separation ensures that CI/CD operations (which can be high-volume) don't impact the performance of core control plane functions.

### Database Models

#### Main Database Models
- `Service` - Registered services in the cluster
- `ServiceHealth` - Health check history
- `ServiceAction` - Available actions per service
- `TemplateDeployment` - Deployment templates
- `DeploymentLog` - Deployment execution logs
- `Dashboard` - User dashboards
- `UserFavorite` - Favorite services per user
- `APIToken` - API authentication tokens

#### CI/CD Database Models
- `Pipeline` - CI/CD pipeline executions
- `PipelineStage` - Individual stages within pipelines
- `PipelineMetric` - Performance metrics for pipelines

## Development Setup

### Running Tests

The test suite sets up both databases automatically:

```bash
cd backend
./run_tests.sh
```

The test script (`run_tests.sh`):
1. Creates `thinkube_control_test` database for main models
2. Creates `cicd_monitoring_test` database for CI/CD models
3. Drops and recreates all tables to ensure clean state
4. Runs the test suite with coverage

### Test Database Configuration

Tests use separate database fixtures:
- `test_db` - Main database session
- `test_cicd_db` - CI/CD database session

Example test that uses both databases:
```python
def test_with_both_databases(client, test_db, test_cicd_db):
    # client uses test_db by default
    # For CI/CD endpoints, override the dependency:
    client.app.dependency_overrides[get_cicd_db] = lambda: test_cicd_db
```

## Deployment

### Development Deployment

For rapid development iteration:
```bash
cd ~/thinkube
./scripts/run_ansible.sh ansible/40_thinkube/core/thinkube-control/12_deploy_dev.yaml
```

This playbook:
1. Updates code from GitHub template using Copier
2. Pushes to Gitea
3. Triggers webhook → CI/CD build → deployment

### Production Deployment

```bash
cd ~/thinkube
./scripts/run_ansible.sh ansible/40_thinkube/core/thinkube-control/10_deploy.yaml
```

## CI/CD Integration

### Build Process

1. **Webhook Trigger**: Gitea sends webhook on push
2. **Argo Events**: Receives webhook and triggers workflow
3. **Test Stage**: Runs tests in both databases
4. **Build Stage**: Creates container images if tests pass
5. **Deploy Stage**: ArgoCD deploys new images

### Workflow Template

The build workflow (`build-workflow.yaml.jinja`):
- Runs on the master node where code is mounted
- Tests must pass before builds proceed
- Uses Kaniko for in-cluster container builds

## API Endpoints

### Service Discovery
- `GET /api/v1/services` - List all services
- `GET /api/v1/services/{service_id}` - Service details
- `GET /api/v1/services/{service_id}/health` - Health history
- `POST /api/v1/services/{service_id}/actions/{action}` - Execute action

### CI/CD Monitoring
- `GET /api/v1/cicd/pipelines` - List pipelines
- `GET /api/v1/cicd/pipelines/{pipeline_id}` - Pipeline details
- `GET /api/v1/cicd/pipelines/{pipeline_id}/stages` - Stage details
- `POST /api/v1/cicd/events` - Record pipeline events

### Deployment Management
- `GET /api/v1/templates` - List deployment templates
- `POST /api/v1/templates/{template_id}/deploy` - Deploy template
- `GET /api/v1/deployments/{deployment_id}/logs` - Deployment logs

## Environment Variables

### Required
- `POSTGRES_USER` - Database username
- `POSTGRES_PASSWORD` - Database password
- `POSTGRES_HOST` - Database host
- `POSTGRES_PORT` - Database port (default: 5432)
- `POSTGRES_DB` - Main database name
- `CICD_DB_NAME` - CI/CD database name
- `KEYCLOAK_URL` - Keycloak server URL
- `KEYCLOAK_REALM` - Keycloak realm
- `KEYCLOAK_CLIENT_ID` - OAuth client ID
- `KEYCLOAK_CLIENT_SECRET` - OAuth client secret

### Optional
- `SECRET_KEY` - JWT secret (auto-generated if not set)
- `FRONTEND_URL` - Frontend URL for CORS
- `LOG_LEVEL` - Logging level (default: INFO)

## Troubleshooting

### Database Connection Issues

If you see "column does not exist" errors:
1. Check which database the table belongs to
2. Ensure both databases are properly initialized
3. Verify the model is imported in the correct `__init__.py`

### Test Failures

Common issues:
- **Missing tables**: Check if `run_tests.sh` creates tables in both databases
- **Connection errors**: Verify PostgreSQL is accessible
- **Import errors**: Ensure all models are in `app/models/__init__.py`

### CI/CD Pipeline Failures

1. Check Argo Events logs:
   ```bash
   kubectl logs -n argo deploy/eventsource-gitea-webhook
   ```

2. Check workflow status:
   ```bash
   kubectl get workflows -n argo
   ```

3. Check test output:
   ```bash
   kubectl logs -n argo <workflow-pod-name> -c main
   ```

## Contributing

1. Make changes to the template repository
2. Test locally with `run_tests.sh`
3. Push to GitHub
4. Deploy with `12_deploy_dev.yaml`
5. Verify CI/CD pipeline passes

## Architecture Decisions

### Why Two Databases?

1. **Performance Isolation**: CI/CD queries don't impact control plane
2. **Scaling**: Can scale databases independently
3. **Backup/Recovery**: Different backup strategies for each use case
4. **Security**: Can apply different access controls

### Why Copier Templates?

1. **Consistency**: Ensures all deployments follow same patterns
2. **Customization**: Each installation can have custom domains/settings
3. **GitOps**: Processed templates go to Gitea for ArgoCD

### Why Test-First CI/CD?

1. **Quality Gates**: Prevents broken code from being deployed
2. **Fast Feedback**: Developers know immediately if changes break
3. **Confidence**: Passing tests mean safe to deploy

## MCP (Model Context Protocol) Integration

Thinkube Control includes a built-in MCP server that exposes deployment functionality to AI assistants like Claude Code. This allows AI tools to interact with the platform programmatically.

### MCP Architecture

The MCP server is integrated directly into the FastAPI backend using FastMCP's `from_fastapi()` functionality:

1. **Automatic Tool Generation**: All FastAPI endpoints are automatically exposed as MCP tools
2. **HTTP Transport**: Uses standard HTTP transport (SSE is deprecated)
3. **Authentication**: Uses service account token for internal authentication
4. **Endpoint**: Available at `/api/mcp/mcp/` (note the double path)

### Authentication Flow

The MCP integration uses a CI/CD monitoring token for authentication:

1. **Token Creation Timing**:
   - Kubernetes secret created BEFORE git push (so pods can start)
   - Database entry created AFTER deployment (when tables exist)
   - This handles the circular dependency elegantly

2. **Environment Variable**:
   - Backend reads `CICD_MONITORING_TOKEN` at startup
   - Configures MCP's httpx client with Bearer authentication
   - Allows MCP to call protected FastAPI endpoints

3. **Token Management**:
   ```yaml
   # In backend-deployment.yaml.jinja
   - name: CICD_MONITORING_TOKEN
     valueFrom:
       secretKeyRef:
         name: cicd-monitoring-token
         key: token
   ```

### Configuration

The MCP server is configured in `.mcp.json` at the project root:

```json
{
  "mcpServers": {
    "thinkube-control": {
      "type": "http",
      "url": "https://control.example.com/api/mcp/mcp/",
      "headers": {
        "Authorization": "Bearer tk_..."
      }
    }
  }
}
```

### Available MCP Tools

When connected, the MCP server exposes these tools:

- `list_templates()` - List available deployment templates
- `get_template_parameters(template_url)` - Get template parameters
- `deploy_template(template_url, app_name, variables)` - Deploy a template
- `get_deployment_status_by_id(deployment_id)` - Check deployment status
- `get_deployment_logs_by_id(deployment_id)` - Get deployment logs
- `list_recent_deployments()` - List recent deployments
- `cancel_deployment_by_id(deployment_id)` - Cancel a deployment

### Implementation Details

#### Lifespan Management

The MCP server requires proper lifespan management to work with FastAPI:

```python
# Combined lifespan for both FastAPI and MCP
@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with app_lifespan(app):
        async with mcp_app.lifespan(app):
            yield
```

#### Authentication Requirements

**Important**: API endpoints must support dual authentication to work with MCP:
- Use `get_current_user_dual_auth` dependency (supports both Keycloak JWT and API tokens)
- NOT `get_current_active_user` (Keycloak JWT only)
- Access user properties with dict notation: `current_user.get('sub')` not `current_user.sub`

The MCP server authenticates using the CI/CD monitoring API token configured at startup.

### Deployment Process

1. **Playbook 12_deploy.yaml**:
   - Creates CI/CD monitoring token secret
   - Processes templates and pushes to Gitea
   - Webhook triggers build and deployment

2. **Backend Startup**:
   - Reads CICD_MONITORING_TOKEN from environment
   - Configures MCP server with authentication
   - Mounts MCP at `/api/mcp`

3. **Playbook 13_configure_code_server.yaml**:
   - Reads token from thinkube-control namespace
   - Configures VS Code extension
   - Updates `.mcp.json` for Claude Code

### Troubleshooting MCP

#### Connection Issues

1. **Check MCP Status**:
   ```bash
   # In Claude Code, run:
   /mcp
   ```

2. **Verify Endpoint**:
   ```bash
   curl https://control.example.com/api/mcp/mcp/
   ```

3. **Check Token**:
   - Ensure CICD_MONITORING_TOKEN is set in pod
   - Verify token exists in cicd-monitoring-token secret

#### Common Problems

- **"MCP server failed to connect"**: Check transport type is "http" not "sse"
- **401 Unauthorized errors**: 
  - Verify token in `.mcp.json` matches the one in Kubernetes secret
  - Ensure endpoints use `get_current_user_dual_auth` not `get_current_active_user`
  - Check token exists in database: `SELECT name FROM api_tokens WHERE name = 'CI/CD Monitoring Token'`
- **AttributeError: 'dict' object has no attribute 'sub'**:
  - Endpoints must use dict notation: `current_user.get('sub')` not `current_user.sub`
  - This happens when switching from Keycloak-only to dual auth
- **404 errors**: Ensure URL ends with `/api/mcp/mcp/` (double path)
- **Lifespan errors**: Check combined lifespan is properly configured

## License

Part of the Thinkube platform - see main repository for license details.