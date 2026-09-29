import json
import os
from datetime import datetime, timezone

import boto3
from flask import Flask, jsonify, render_template

app = Flask(__name__)

AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
LAMBDA_FUNCTION_NAME = os.getenv("COSTGUARD_LAMBDA_FUNCTION")

lambda_client = boto3.client(
    "lambda",
    region_name=AWS_REGION
)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/scan")
def api_scan():

    if not LAMBDA_FUNCTION_NAME:
        return jsonify({
            "success": False,
            "error": (
                "Lambda is not configured. "
                "Set COSTGUARD_LAMBDA_FUNCTION before starting Flask."
            )
        }), 503

    try:

        # Invoke AWS Lambda
        response = lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps({
                "source": "costguard-dashboard",
                "action": "scan"
            }).encode("utf-8")
        )

        payload = response["Payload"].read()

        if response.get("FunctionError"):
            raise RuntimeError(
                payload.decode("utf-8", errors="replace")
            )

        lambda_data = json.loads(payload or b"{}")

        if not lambda_data.get("success"):
            raise RuntimeError(
                lambda_data.get(
                    "error",
                    "Lambda scan failed."
                )
            )

        resources = []

        # -------------------------
        # EC2 RESOURCES
        # -------------------------

        for instance in lambda_data.get("ec2", []):

            cpu = instance.get(
                "cpu_average_last_hour_percent"
            )

            state = instance.get(
                "state",
                "unknown"
            )

            savings = instance.get(
                "estimated_monthly_savings_inr",
                0
            ) or 0

            if state == "stopped":

                issue = "Stopped"

                recommendation = (
                    "Confirm the instance is still required."
                )

            elif cpu is not None and cpu < 10:

                issue = "Underutilized"

                recommendation = (
                    "Review CPU history and consider right-sizing."
                )

            else:

                issue = "Normal"

                recommendation = (
                    "No action required."
                )

            resources.append({

                "type": "EC2",

                "id": instance.get(
                    "instance_id"
                ),

                "name": instance.get(
                    "name"
                ),

                "details": instance.get(
                    "instance_type",
                    "unknown"
                ),

                "state": state,

                "cpu": cpu,

                "issue": issue,

                "recommendation": recommendation,

                "estimated_monthly_savings_inr": savings

            })


        # -------------------------
        # EBS RESOURCES
        # -------------------------

        for volume in lambda_data.get("ebs", []):

            size = volume.get(
                "size_gb",
                0
            )

            state = volume.get(
                "state",
                "unknown"
            )

            attached = volume.get(
                "attached_instance"
            )

            if attached:

                issue = "Normal"

                recommendation = (
                    "No action required."
                )

                savings = 0

            else:

                issue = "Unused"

                recommendation = (
                    "Review and delete only if no longer required."
                )

                savings = round(
                    size * 7,
                    2
                )

            resources.append({

                "type": "EBS",

                "id": volume.get(
                    "volume_id"
                ),

                "name": volume.get(
                    "volume_id"
                ),

                "details": (
                    f"{size} GB "
                    f"{volume.get('volume_type', 'unknown')}"
                ),

                "state": (
                    "Attached"
                    if attached
                    else state
                ),

                "cpu": None,

                "issue": issue,

                "recommendation": recommendation,

                "estimated_monthly_savings_inr": savings

            })


        # -------------------------
        # S3 RESOURCES
        # -------------------------

        for bucket in lambda_data.get("s3", []):

            resources.append({

                "type": "S3",

                "id": bucket.get(
                    "bucket_name"
                ),

                "name": bucket.get(
                    "bucket_name"
                ),

                "details": "Bucket",

                "state": "Active",

                "cpu": None,

                "issue": "Review",

                "recommendation": bucket.get(
                    "estimated_saving_note",
                    "Review bucket storage and lifecycle."
                ),

                "estimated_monthly_savings_inr": 0

            })


        # -------------------------
        # SUMMARY
        # -------------------------

        waste_issues = {
            "Underutilized",
            "Stopped",
            "Unused",
            "Old Objects"
        }

        waste_resources = [
            resource
            for resource in resources
            if resource["issue"] in waste_issues
        ]

        total_resources = len(resources)

        potential_waste = len(
            waste_resources
        )

        suggestions = potential_waste

        monthly_savings = round(
            sum(
                resource.get(
                    "estimated_monthly_savings_inr",
                    0
                ) or 0

                for resource in resources
            ),
            2
        )


        # -------------------------
        # DASHBOARD RESPONSE
        # -------------------------

        return jsonify({

            "success": True,

            "total": total_resources,

            "waste": potential_waste,

            "suggestions": suggestions,

            "monthly_savings_estimate_inr":
                monthly_savings,

            "scanned_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "region":
                lambda_data.get(
                    "region",
                    AWS_REGION
                ),

            "resources":
                resources,

            "report_key":
                lambda_data.get(
                    "report_key"
                ),

            "savings_estimate_note":
                (
                    "Savings are planning estimates only. "
                    "Confirm actual AWS pricing before taking action."
                )

        })


    except Exception as error:

        app.logger.exception(
            "CostGuard Lambda scan failed"
        )

        return jsonify({

            "success": False,

            "error": str(error)

        }), 502


if __name__ == "__main__":

    app.run(

        debug=True,

        host="127.0.0.1",

        port=5000

    )