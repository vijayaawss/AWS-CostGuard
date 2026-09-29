import json
import os
from datetime import datetime, timedelta, timezone

import boto3


# =========================================================
# CONFIGURATION
# =========================================================

REGION = os.getenv(
    "AWS_REGION",
    "ap-south-1"
)

RESULTS_BUCKET = os.environ[
    "RESULTS_BUCKET"
]

SNS_TOPIC_ARN = os.getenv(
    "SNS_TOPIC_ARN"
)

EBS_COST_PER_GB = float(
    os.getenv(
        "COSTGUARD_EBS_INR_PER_GB_MONTH",
        "7"
    )
)

S3_SAVING_PER_GB = float(
    os.getenv(
        "COSTGUARD_S3_INR_SAVING_PER_GB_MONTH",
        "1"
    )
)


# =========================================================
# AWS CLIENTS
# =========================================================

ec2 = boto3.client(
    "ec2",
    region_name=REGION
)

cloudwatch = boto3.client(
    "cloudwatch",
    region_name=REGION
)

s3 = boto3.client(
    "s3",
    region_name=REGION
)

sns = boto3.client(
    "sns",
    region_name=REGION
)


# =========================================================
# EC2 SCAN
# =========================================================

def get_ec2_resources():

    response = ec2.describe_instances()

    instances = []

    for reservation in response.get(
        "Reservations",
        []
    ):

        for instance in reservation.get(
            "Instances",
            []
        ):

            instance_id = instance[
                "InstanceId"
            ]

            state = instance.get(
                "State",
                {}
            ).get(
                "Name",
                "unknown"
            )

            instance_type = instance.get(
                "InstanceType",
                "unknown"
            )

            name = instance_id

            for tag in instance.get(
                "Tags",
                []
            ):

                if tag.get("Key") == "Name":

                    name = tag.get(
                        "Value",
                        instance_id
                    )

            instances.append({

                "instance_id":
                    instance_id,

                "name":
                    name,

                "instance_type":
                    instance_type,

                "state":
                    state

            })

    return instances


# =========================================================
# EBS SCAN
# =========================================================

def get_ebs_resources():

    response = ec2.describe_volumes()

    volumes = []

    for volume in response.get(
        "Volumes",
        []
    ):

        volume_id = volume[
            "VolumeId"
        ]

        size = volume.get(
            "Size",
            0
        )

        volume_type = volume.get(
            "VolumeType",
            "unknown"
        )

        state = volume.get(
            "State",
            "unknown"
        )

        attachments = volume.get(
            "Attachments",
            []
        )

        attached_instance = None

        if attachments:

            attached_instance = (
                attachments[0].get(
                    "InstanceId"
                )
            )

        estimated_monthly_cost = (
            size *
            EBS_COST_PER_GB
        )

        volumes.append({

            "volume_id":
                volume_id,

            "size_gb":
                size,

            "volume_type":
                volume_type,

            "state":
                state,

            "attached_instance":
                attached_instance,

            "estimated_monthly_cost_inr":
                round(
                    estimated_monthly_cost,
                    2
                )

        })

    return volumes


# =========================================================
# CLOUDWATCH CPU
# =========================================================

def get_cpu_utilization(
    instance_id
):

    end_time = datetime.now(
        timezone.utc
    )

    start_time = (
        end_time -
        timedelta(hours=1)
    )

    response = (
        cloudwatch.get_metric_statistics(
            Namespace="AWS/EC2",

            MetricName=
                "CPUUtilization",

            Dimensions=[
                {
                    "Name":
                        "InstanceId",

                    "Value":
                        instance_id
                }
            ],

            StartTime=
                start_time,

            EndTime=
                end_time,

            Period=
                3600,

            Statistics=[
                "Average"
            ]
        )
    )

    datapoints = response.get(
        "Datapoints",
        []
    )

    if not datapoints:

        return None

    return round(
        datapoints[-1][
            "Average"
        ],
        2
    )


# =========================================================
# S3 SCAN
# =========================================================

def get_s3_buckets():

    response = s3.list_buckets()

    buckets = []

    for bucket in response.get(
        "Buckets",
        []
    ):

        bucket_name = bucket[
            "Name"
        ]

        object_count = 0

        total_size_bytes = 0

        try:

            paginator = (
                s3.get_paginator(
                    "list_objects_v2"
                )
            )

            for page in paginator.paginate(
                Bucket=bucket_name
            ):

                for obj in page.get(
                    "Contents",
                    []
                ):

                    object_count += 1

                    total_size_bytes += (
                        obj.get(
                            "Size",
                            0
                        )
                    )

            total_size_mb = round(
                total_size_bytes /
                (1024 * 1024),
                2
            )

            total_size_gb = round(
                total_size_bytes /
                (1024 * 1024 * 1024),
                4
            )

            if bucket_name == RESULTS_BUCKET:

                note = (
                    "CostGuard report bucket - "
                    "review storage periodically."
                )

            else:

                note = (
                    "Review bucket storage and "
                    "lifecycle policies."
                )

            buckets.append({

                "bucket_name":
                    bucket_name,

                "object_count":
                    object_count,

                "total_size_mb":
                    total_size_mb,

                "total_size_gb":
                    total_size_gb,

                "estimated_saving_note":
                    note

            })

        except Exception as error:

            buckets.append({

                "bucket_name":
                    bucket_name,

                "object_count":
                    0,

                "total_size_mb":
                    0,

                "total_size_gb":
                    0,

                "estimated_saving_note":
                    (
                        "Unable to inspect objects: "
                        f"{error}"
                    )

            })

    return buckets


# =========================================================
# SAVE REPORT TO S3
# =========================================================

def save_report(report):

    now = datetime.now(
        timezone.utc
    )

    date_path = now.strftime(
        "%Y/%m/%d"
    )

    timestamp = now.strftime(
        "%Y%m%d-%H%M%S"
    )

    report_key = (
        f"scans/{date_path}/"
        f"costguard-{timestamp}.json"
    )

    s3.put_object(

        Bucket=RESULTS_BUCKET,

        Key=report_key,

        Body=json.dumps(
            report,
            indent=2,
            default=str
        ).encode("utf-8"),

        ContentType=
            "application/json"
    )

    return report_key


# =========================================================
# SNS NOTIFICATION
# =========================================================

def send_sns_notification(
    report,
    report_key
):

    if not SNS_TOPIC_ARN:

        return

    summary = report[
        "summary"
    ]

    message = f"""
AWS CostGuard Scan Completed

Resources:
EC2: {summary["ec2_instances"]} | EBS: {summary["ebs_volumes"]} | S3: {summary["s3_buckets_reviewed"]}

Running: {sum(1 for x in report["ec2"] if x["state"] == "running")} | Stopped: {sum(1 for x in report["ec2"] if x["state"] == "stopped")}

Recommendations:
• {sum(1 for x in report["ec2"] if x["state"] == "stopped")} stopped EC2 instance(s)
• {sum(1 for x in report["ec2"] if x.get("cpu_average_last_hour_percent") is not None and x["cpu_average_last_hour_percent"] < 10)} underutilized EC2 instance(s)

Report:
s3://{RESULTS_BUCKET}/{report_key}

Region: {REGION}

"""

    sns.publish(

        TopicArn=
            SNS_TOPIC_ARN,

        Subject=
            "AWS CostGuard Scan Completed",

        Message=
            message
    )


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(
    event,
    context
):

    try:

        # =================================================
        # 1. EC2
        # =================================================

        instances = (
            get_ec2_resources()
        )

        for instance in instances:

            try:

                cpu = (
                    get_cpu_utilization(
                        instance[
                            "instance_id"
                        ]
                    )
                )

                instance[
                    "cpu_average_last_hour_percent"
                ] = cpu

            except Exception as error:

                instance[
                    "cloudwatch_error"
                ] = str(error)


        # =================================================
        # 2. EBS
        # =================================================

        volumes = (
            get_ebs_resources()
        )


        # =================================================
        # 3. S3
        # =================================================

        buckets = (
            get_s3_buckets()
        )


        # =================================================
        # 4. EBS COST
        # =================================================

        total_ebs_gb = sum(

            volume[
                "size_gb"
            ]

            for volume in volumes
        )

        estimated_ebs_cost = (
            total_ebs_gb *
            EBS_COST_PER_GB
        )


        # =================================================
        # 5. S3 STORAGE
        # =================================================

        total_s3_objects = sum(

            bucket[
                "object_count"
            ]

            for bucket in buckets
        )

        total_s3_size_mb = round(

            sum(

                bucket[
                    "total_size_mb"
                ]

                for bucket in buckets

            ),

            2
        )

        total_s3_size_gb = round(

            sum(

                bucket[
                    "total_size_gb"
                ]

                for bucket in buckets

            ),

            4
        )


        # =================================================
        # 6. REPORT
        # =================================================

        report = {

            "success":
                True,

            "project":
                "AWS CostGuard",

            "scan_time_utc":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "region":
                REGION,

            "summary": {

                "ec2_instances":
                    len(instances),

                "ebs_volumes":
                    len(volumes),

                "total_ebs_gb":
                    total_ebs_gb,

                "estimated_ebs_monthly_cost_inr":
                    round(
                        estimated_ebs_cost,
                        2
                    ),

                "s3_buckets_reviewed":
                    len(buckets),

                "s3_total_objects":
                    total_s3_objects,

                "s3_total_size_mb":
                    total_s3_size_mb,

                "s3_total_size_gb":
                    total_s3_size_gb

            },

            "ec2":
                instances,

            "ebs":
                volumes,

            "s3":
                buckets,

            "cost_assumptions": {

                "ebs_inr_per_gb_month":
                    EBS_COST_PER_GB,

                "s3_saving_inr_per_gb_month":
                    S3_SAVING_PER_GB

            }

        }


        # =================================================
        # 7. SAVE REPORT
        # =================================================

        report_key = (
            save_report(
                report
            )
        )


        # =================================================
        # 8. SEND SNS EMAIL
        # =================================================

        try:

            send_sns_notification(
                report,
                report_key
            )

        except Exception as sns_error:

            # Don't fail the entire scan
            # if SNS has a problem.

            print(
                "SNS notification failed:",
                str(sns_error)
            )


        # =================================================
        # 9. RETURN TO FLASK DASHBOARD
        # =================================================

        return {

            "success":
                True,

            "region":
                REGION,

            "report_key":
                report_key,

            "summary":
                report[
                    "summary"
                ],

            "ec2":
                instances,

            "ebs":
                volumes,

            "s3":
                buckets

        }


    except Exception as error:

        return {

            "success":
                False,

            "error":
                str(error)

        }