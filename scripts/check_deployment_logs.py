#!/usr/bin/env python3
"""
Check deployment logs directly from the database
Usage: python check_deployment_logs.py [deployment_id]
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker
from app.models.deployments import TemplateDeployment, DeploymentLog
from app.core.config import settings
import json
from datetime import datetime

def main():
    # Create database connection
    engine = create_engine(settings.DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        if len(sys.argv) > 1:
            # Check specific deployment
            deployment_id = sys.argv[1]
            deployment = session.query(TemplateDeployment).filter_by(id=deployment_id).first()
            
            if not deployment:
                print(f"Deployment {deployment_id} not found")
                return
            
            print(f"\n=== Deployment Details ===")
            print(f"ID: {deployment.id}")
            print(f"Name: {deployment.name}")
            print(f"Status: {deployment.status}")
            print(f"Created: {deployment.created_at}")
            print(f"Started: {deployment.started_at}")
            print(f"Completed: {deployment.completed_at}")
            print(f"Output: {deployment.output}")
            print(f"\nVariables:")
            for k, v in deployment.variables.items():
                if 'password' not in k.lower() and 'token' not in k.lower():
                    print(f"  {k}: {v}")
            
            # Get logs
            logs = session.query(DeploymentLog)\
                          .filter_by(deployment_id=deployment_id)\
                          .order_by(DeploymentLog.timestamp)\
                          .all()
            
            print(f"\n=== Deployment Logs ({len(logs)} entries) ===")
            for log in logs:
                print(f"[{log.timestamp}] {log.type}: {log.message}")
                if log.task_name:
                    print(f"  Task: {log.task_name}")
            
        else:
            # List recent deployments
            deployments = session.query(TemplateDeployment)\
                                .order_by(desc(TemplateDeployment.created_at))\
                                .limit(10)\
                                .all()
            
            print("\n=== Recent Deployments ===")
            for d in deployments:
                print(f"\nID: {d.id}")
                print(f"Name: {d.name}")
                print(f"Status: {d.status}")
                print(f"Created: {d.created_at}")
                print(f"Template: {d.template_url}")
                
                # Count logs
                log_count = session.query(DeploymentLog)\
                                  .filter_by(deployment_id=str(d.id))\
                                  .count()
                print(f"Log entries: {log_count}")
                
    finally:
        session.close()

if __name__ == "__main__":
    main()