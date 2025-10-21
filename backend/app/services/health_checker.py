"""Service health checking functionality"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from uuid import uuid4
import httpx
import asyncpg
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.services import Service as ServiceModel, ServiceHealth, ServiceEndpoint
from app.db.session import SessionLocal


logger = logging.getLogger(__name__)


class HealthCheckService:
    """Background service for health monitoring"""

    def __init__(
        self,
        check_interval: int = 120,  # seconds - one check every 2 minutes
        timeout: int = 10,  # seconds
        cleanup_after_hours: int = 24,  # hours
    ):
        """Initialize health check service

        Args:
            check_interval: Interval between health checks in seconds
            timeout: Timeout for health check requests in seconds
            cleanup_after_hours: Delete health records older than this many hours
        """
        self.check_interval = check_interval
        self.timeout = timeout
        self.cleanup_after_hours = cleanup_after_hours
        self.is_running = False

    async def start(self):
        """Start the health check background task"""
        if self.is_running:
            logger.warning("Health check service is already running")
            return

        self.is_running = True
        logger.info(
            f"Starting health check service with {self.check_interval}s interval"
        )

        # Run initial health check immediately
        await self.run_health_checks()

        # Then run periodically
        while self.is_running:
            await asyncio.sleep(self.check_interval)
            if self.is_running:
                await self.run_health_checks()

    def stop(self):
        """Stop the health check service"""
        self.is_running = False
        logger.info("Stopping health check service")

    async def run_health_checks(self):
        """Run health checks for all enabled services"""
        session_factory = SessionLocal()
        db: Session = session_factory()
        try:
            # Get all enabled services with health endpoints
            services = (
                db.query(ServiceModel)
                .filter(
                    and_(
                        ServiceModel.is_enabled == True,
                        ServiceModel.health_endpoint.isnot(None),
                    )
                )
                .all()
            )

            logger.debug(f"Running health checks for {len(services)} services")

            # Run health checks concurrently
            tasks = []
            for service in services:
                task = self.check_service_health(service, db)
                tasks.append(task)

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # Cleanup old health records
            await self.cleanup_old_records(db)

            db.commit()

        except Exception as e:
            logger.error(f"Error running health checks: {e}")
            db.rollback()
        finally:
            db.close()

    async def check_endpoint_health(self, endpoint: ServiceEndpoint) -> Dict[str, Any]:
        """Check health of a specific endpoint

        Args:
            endpoint: Endpoint to check

        Returns:
            Dictionary with health check results
        """
        # Skip only if there's no URL at all, but allow internal endpoints with health URLs
        if not endpoint.url and not endpoint.health_url:
            return {"status": "unknown", "reason": "No URL configured"}

        start_time = datetime.utcnow()

        try:
            if endpoint.type in ["http", "internal"]:
                # HTTP health check (also for internal cluster endpoints)
                health_url = endpoint.health_url

                async with httpx.AsyncClient(
                    verify=False, timeout=self.timeout
                ) as client:
                    response = await client.get(health_url)
                    response_time = int(
                        (datetime.utcnow() - start_time).total_seconds() * 1000
                    )

                    # Consider various status codes as healthy:
                    # - 2xx: Success
                    # - 3xx: Redirects (service is running)
                    # - 401/403: Authentication required (service is running but needs auth)
                    is_healthy = (
                        200 <= response.status_code < 400
                    ) or response.status_code in [401, 403]

                    return {
                        "status": "healthy" if is_healthy else "unhealthy",
                        "status_code": response.status_code,
                        "response_time": response_time,
                    }

            elif endpoint.type == "postgres":
                # PostgreSQL health check - try to connect without credentials
                # Authentication errors mean the service is running (just needs auth)
                import asyncpg
                from urllib.parse import urlparse

                try:
                    # Parse the PostgreSQL URL to ensure we have the database name
                    parsed = urlparse(endpoint.health_url or endpoint.url)

                    # Try to connect - we expect this to fail with auth error if healthy
                    conn = await asyncpg.connect(
                        endpoint.health_url or endpoint.url,
                        timeout=self.timeout
                    )

                    # If we actually connect (unlikely without creds), it's healthy
                    await conn.close()

                    response_time = int(
                        (datetime.utcnow() - start_time).total_seconds() * 1000
                    )

                    return {
                        "status": "healthy",
                        "response_time": response_time,
                        "details": {"connected": True}
                    }

                except asyncpg.InvalidPasswordError:
                    # Authentication failed = PostgreSQL is running and responding!
                    response_time = int(
                        (datetime.utcnow() - start_time).total_seconds() * 1000
                    )
                    return {
                        "status": "healthy",
                        "response_time": response_time,
                        "details": {"check_type": "auth_response", "message": "Service is running (auth required)"}
                    }

                except asyncpg.InvalidAuthorizationSpecificationError:
                    # No password supplied = PostgreSQL is running
                    response_time = int(
                        (datetime.utcnow() - start_time).total_seconds() * 1000
                    )
                    return {
                        "status": "healthy",
                        "response_time": response_time,
                        "details": {"check_type": "auth_response", "message": "Service is running (auth required)"}
                    }

                except (asyncpg.CannotConnectNowError, asyncpg.ConnectionDoesNotExistError, ConnectionRefusedError) as e:
                    # Connection refused = service is actually down
                    return {
                        "status": "unhealthy",
                        "error": f"Cannot connect: {str(e)}",
                        "response_time": int(
                            (datetime.utcnow() - start_time).total_seconds() * 1000
                        )
                    }

                except Exception as e:
                    # Check if it's an auth-related error message
                    error_msg = str(e).lower()
                    if 'password' in error_msg or 'authentication' in error_msg or 'auth' in error_msg:
                        # Auth errors mean the service is up
                        response_time = int(
                            (datetime.utcnow() - start_time).total_seconds() * 1000
                        )
                        return {
                            "status": "healthy",
                            "response_time": response_time,
                            "details": {"check_type": "auth_response", "message": "Service is running (auth required)"}
                        }
                    else:
                        # Other errors might mean it's down
                        return {
                            "status": "unhealthy",
                            "error": f"Health check failed: {str(e)}",
                            "response_time": int(
                                (datetime.utcnow() - start_time).total_seconds() * 1000
                            )
                        }

            elif endpoint.type == "grpc":
                # TODO: Implement gRPC health check
                return {
                    "status": "unknown",
                    "reason": "gRPC health check not implemented",
                }

            else:
                # Other endpoint types
                return {
                    "status": "unknown",
                    "reason": f"Health check not supported for {endpoint.type}",
                }

        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "checked_at": datetime.utcnow().isoformat(),
            }

    async def check_service_health(self, service: ServiceModel, db: Session):
        """Check individual service health

        Args:
            service: Service to check
            db: Database session
        """
        start_time = datetime.utcnow()
        health_record = ServiceHealth(service_id=service.id, checked_at=start_time)

        try:
            # Check if service has endpoints
            if not service.endpoints:
                logger.warning(f"Service {service.name} has no endpoints configured")
                health_record.status = "unknown"
                health_record.error_message = "No endpoints configured"
                db.add(health_record)
                return

            # Check primary endpoint first, fallback to others
            primary_endpoint = None
            for ep in service.endpoints:
                if ep.is_primary:
                    primary_endpoint = ep
                    break

            # If no primary, use first non-internal endpoint
            if not primary_endpoint:
                for ep in service.endpoints:
                    if not ep.is_internal:
                        primary_endpoint = ep
                        break

            if not primary_endpoint:
                logger.warning(f"Service {service.name} has no checkable endpoints")
                health_record.status = "unknown"
                health_record.error_message = "No checkable endpoints"
                db.add(health_record)
                return

            # Perform health check on primary endpoint
            result = await self.check_endpoint_health(primary_endpoint)

            # Update endpoint health status
            primary_endpoint.last_health_check = datetime.utcnow()
            primary_endpoint.health_status = result.get("status", "unknown")

            # Update service health record
            health_record.status = result.get("status", "unknown")
            health_record.response_time = result.get("response_time")
            health_record.status_code = result.get("status_code")
            health_record.error_message = result.get("error") or result.get("reason")
            health_record.details = result

            # Also check other endpoints asynchronously (non-blocking)
            endpoint_results = {}
            for ep in service.endpoints:
                if ep != primary_endpoint and not ep.is_internal:
                    ep_result = await self.check_endpoint_health(ep)
                    ep.last_health_check = datetime.utcnow()
                    ep.health_status = ep_result.get("status", "unknown")
                    endpoint_results[ep.name] = ep_result

            if endpoint_results:
                health_record.details["endpoints"] = endpoint_results

            # Determine overall health status
            if result.get("status") == "healthy":
                health_record.status = "healthy"

                pass

            logger.debug(
                f"Health check for {service.name}: {health_record.status} "
                f"(endpoint: {primary_endpoint.name})"
            )

        except httpx.TimeoutException:
            health_record.status = "unhealthy"
            health_record.error_message = "Health check timeout"
            health_record.response_time = self.timeout * 1000
            logger.warning(f"Health check timeout for {service.name}")

        except httpx.ConnectError as e:
            health_record.status = "unhealthy"
            health_record.error_message = f"Connection failed: {str(e)}"
            logger.warning(f"Connection failed for {service.name}: {e}")

        except Exception as e:
            health_record.status = "unknown"
            health_record.error_message = f"Health check error: {str(e)}"
            logger.error(f"Health check error for {service.name}: {e}")

        # Save health record
        db.add(health_record)

    async def cleanup_old_records(self, db: Session):
        """Clean up old health check records

        Args:
            db: Database session
        """
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=self.cleanup_after_hours)

            # Delete old records
            deleted_count = (
                db.query(ServiceHealth)
                .filter(ServiceHealth.checked_at < cutoff_time)
                .delete()
            )

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old health records")

        except Exception as e:
            logger.error(f"Error cleaning up old health records: {e}")

    async def get_service_health_history(
        self, service_id: str, hours: int = 24
    ) -> Dict[str, Any]:
        """Get health history for a service

        Args:
            service_id: Service UUID
            hours: Number of hours of history to retrieve

        Returns:
            Dictionary with health statistics and history
        """
        session_factory = SessionLocal()
        db: Session = session_factory()
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)

            # Get health records
            health_records = (
                db.query(ServiceHealth)
                .filter(
                    and_(
                        ServiceHealth.service_id == service_id,
                        ServiceHealth.checked_at >= cutoff_time,
                    )
                )
                .order_by(ServiceHealth.checked_at.desc())
                .all()
            )

            if not health_records:
                return {
                    "current_status": "unknown",
                    "uptime_percentage": 0.0,
                    "health_history": [],
                    "total_checks": 0,
                    "failed_checks": 0,
                }

            # Use consistent time reference
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(hours=hours)

            # Create a dictionary of actual health records by 2-minute interval
            records_by_interval = {}
            for record in health_records:
                # Round to nearest 2-minute interval
                interval_key = record.checked_at.replace(second=0, microsecond=0)
                interval_key = interval_key.replace(minute=(interval_key.minute // 2) * 2)
                records_by_interval[interval_key] = record

            # Generate all 2-minute intervals in the requested period (30 per hour)
            filled_history = []
            intervals_per_hour = 30  # 60 minutes / 2 minutes per check
            total_intervals = int(hours * intervals_per_hour)

            for i in range(total_intervals):
                check_time = end_time - timedelta(minutes=i * 2)
                interval_key = check_time.replace(second=0, microsecond=0)
                interval_key = interval_key.replace(minute=(interval_key.minute // 2) * 2)

                if interval_key in records_by_interval:
                    # We have actual health check data
                    record = records_by_interval[interval_key]
                    filled_history.append({
                        "status": record.status,
                        "checked_at": record.checked_at.isoformat(),
                        "response_time": record.response_time
                    })
                else:
                    # No data - monitoring gap
                    filled_history.append({
                        "status": "unknown",
                        "checked_at": interval_key.isoformat()
                    })

            # Sort by time (oldest first for chart display)
            filled_history.reverse()

            # Calculate statistics
            total_checks = len(filled_history)
            actual_checks = len(health_records)
            monitored_intervals = len(records_by_interval)

            # Count status distribution
            healthy_count = sum(1 for item in filled_history if item["status"] == "healthy")
            unhealthy_count = sum(1 for item in filled_history if item["status"] == "unhealthy")
            unknown_count = sum(1 for item in filled_history if item["status"] == "unknown")

            # Calculate percentages
            uptime_percentage = (healthy_count / total_checks * 100) if total_checks > 0 else 0
            monitoring_coverage = (monitored_intervals / total_checks * 100) if total_checks > 0 else 0

            # Get current status
            current_status = health_records[0].status if health_records else "unknown"

            return {
                "current_status": current_status,
                "uptime_percentage": round(uptime_percentage, 2),
                "monitoring_coverage": round(monitoring_coverage, 2),
                "health_history": filled_history,
                "total_checks": total_checks,
                "actual_checks": actual_checks,
                "healthy_count": healthy_count,
                "unhealthy_count": unhealthy_count,
                "unknown_count": unknown_count,
                "check_interval_seconds": 120
            }

        finally:
            db.close()

    async def check_single_service(self, service_id: str) -> Dict[str, Any]:
        """Perform a manual health check for a single service

        Args:
            service_id: Service UUID

        Returns:
            Health check result
        """
        session_factory = SessionLocal()
        db: Session = session_factory()
        try:
            service = (
                db.query(ServiceModel).filter(ServiceModel.id == service_id).first()
            )
            if not service:
                raise ValueError(f"Service {service_id} not found")

            if not service.is_enabled:
                return {"status": "disabled", "message": "Service is disabled"}

            # Run health check
            await self.check_service_health(service, db)
            db.commit()

            # Get the latest health record
            latest_health = (
                db.query(ServiceHealth)
                .filter(ServiceHealth.service_id == service_id)
                .order_by(ServiceHealth.checked_at.desc())
                .first()
            )

            if latest_health:
                return {
                    "status": latest_health.status,
                    "response_time": latest_health.response_time,
                    "status_code": latest_health.status_code,
                    "error_message": latest_health.error_message,
                    "details": latest_health.details,
                    "checked_at": latest_health.checked_at,
                }
            else:
                return {"status": "unknown", "message": "Health check failed"}

        finally:
            db.close()


# Global instance
health_checker = HealthCheckService()


# 🤖 Generated with [Claude Code](https://claude.ai/code)
