#!/usr/bin/python
# coding:utf-8
########################################################################################################################
# @Author: farmer.luo@gmail.com
# @Create Date: 2019.1.31
#
# @Desc:
# monitor k8s cluster ingress,hpa,pod zabbix script
#
# @Usage:
# 1. import zabbix template
# 2. copy this script to zabbix agent script dir
# 3. Installation package dependency on zabbix agent host:
#    pip install elasticsearch kubernetes elasticsearch_dsl diskcache urllib3 argparse -i https://mirrors.aliyun.com/pypi/simple
# 4. config zabbix agent config and reload zabbix agent
#    agent config userparameter_k8s.conf:
# UserParameter=discover_k8s_hpa,/etc/zabbix/scripts/check_k8s_status.py --discover-hpa
# UserParameter=discover_k8s_ingress,/etc/zabbix/scripts/check_k8s_status.py --discover-ingress
# UserParameter=discover_k8s_pods,/etc/zabbix/scripts/check_k8s_status.py --discover-pods
# UserParameter=k8s_hpa_cpu_utilization[*],/etc/zabbix/scripts/check_k8s_status.py --get-hpa-cpu-utilization --namespace $1 --hpa-name $2
# UserParameter=k8s_hpa_desired_replicas[*],/etc/zabbix/scripts/check_k8s_status.py --get-hpa-desired-replicas --namespace $1 --hpa-name $2
# UserParameter=k8s_hpa_current_replicas[*],/etc/zabbix/scripts/check_k8s_status.py --get-hpa-current-replicas --namespace $1 --hpa-name $2
# UserParameter=k8s_pod_ip[*],/etc/zabbix/scripts/check_k8s_status.py --get-pod-ip --namespace $1 --pod-name $2
# UserParameter=k8s_pod_host_ip[*],/etc/zabbix/scripts/check_k8s_status.py --get-pod-host-ip --namespace $1 --pod-name $2
# UserParameter=k8s_pod_status[*],/etc/zabbix/scripts/check_k8s_status.py --get-pod-status --namespace $1 --pod-name $2
# UserParameter=k8s_pod_restart_count[*],/etc/zabbix/scripts/check_k8s_status.py --get-pod-restart-count --namespace $1 --pod-name $2
# UserParameter=k8s_ingress_status_code_ratio[*],/etc/zabbix/scripts/check_k8s_status.py --get-ingress-status-code-ratio --domain $1 --status-code $2
#
########################################################################################################################

from __future__ import print_function
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search, A
from diskcache import Cache
from pprint import pprint
import urllib3
import sys
import time
import argparse
import json
import warnings

warnings.filterwarnings('ignore')
urllib3.disable_warnings()

# Configs can be set in Configuration class directly or using helper utility
# config.load_kube_config()
conf = client.Configuration()
# k8s1-fe-online
conf.host = "https://10.10.88.20:8443"
conf.api_key['authorization'] = "xxxxxx.xxxxxxx.x-x-xxx-xxx-x"
conf.api_key_prefix["authorization"] = "Bearer"
conf.verify_ssl = False

# elasticsearch server config
es_server = [{"host": "10.16.252.50", "port": 9200},
             {"host": "10.16.252.50", "port": 9200},
             {"host": "10.16.252.50", "port": 9200}
             ]
es_index = "logstash-traefik-ingress-lb-*"
# es查询间隔,ms
es_query_duration = 60000

# 状态码报警及阈值配置
# xxx.com为自定域名的例子
status_code_config = {
    'default': {'403': '90', '404': '90', '500': '2', '502': '2', '499': '70', '406': '70', '503': '5',
                '504': '5', '599': '2', 'other': '30', '429': '5', '430': '1'
                },
    'xxx.com': {'403': '100', '404': '100', '500': '70', '502': '70', '499': '100', '406': '80', '503': '70',
                '504': '70', '599': '0', 'other': '60', '429': '5', '430': '1'
                }
}


def get_status_code(domain):
    if domain in status_code_config:
        return status_code_config[domain]
    else:
        return status_code_config['default']


def get_status_code_ratio(domain):
    es = Elasticsearch(es_server)
    now_time = int(round(time.time() * 1000))
    # BackendName__keyword == BackendName.keyword, gte: >=, lt: <
    s = Search(using=es, index=es_index)\
        .query("match", BackendName__keyword=domain) \
        .filter("range", StartLocal={"gte": now_time - es_query_duration, "lt": now_time})
    # aggregations
    aggs = A('terms', field='DownstreamStatus')
    s.aggs.bucket('tags', aggs)
    s = s[0:0]  # add .size:0
    response = s.execute()
    # print(json.dumps(response.to_dict(), sort_keys=True, indent=2))
    # print(json.dumps(s.to_dict(), sort_keys=True, indent=2))
    data = {'other': 0.0}
    total = float(response.hits.total)
    status_code = get_status_code(domain)
    for tag in response.aggregations.tags.buckets:
        if str(tag.key) in status_code:
            data[str(tag.key)] = round(tag.doc_count / total, 2) * 100
        elif tag.key >= 400:
            data['other'] = data['other'] + round(tag.doc_count / total, 2) * 100
        else:
            continue
    for key in status_code:
        if key not in data:
            data[key] = 0.0
    return data


def get_ingress_status(domain, code):
    cache = Cache('/tmp/check_k8s_status.cache')
    data_cache = cache.get(domain, default=b'', read=True, expire_time=False)
    if domain in cache:
        # print(data_cache)
        if code in data_cache:
            print(data_cache[code])
        else:
            print(0.0)
        exit(0)

    data = get_status_code_ratio(domain)
    expire_time = es_query_duration/1000
    cache.set(domain, data, expire=expire_time)
    if code in data:
        print(data[code])
    else:
        print(0.0)


def discover_ingress():
    data = {"data": []}
    new_client = client.ApiClient(conf)
    api_instance = client.ExtensionsV1beta1Api(new_client)
    try:
        api_response = api_instance.list_ingress_for_all_namespaces(watch=False)
        # pprint(api_response)
        for i in api_response.items:
            # print("%s\t%s\t%s" % (i.metadata.name, i.metadata.namespace, i.spec.rules[0].host))
            status_code = get_status_code(i.spec.rules[0].host)
            for k, v in status_code.items():
                tmp = {"{#INGRESS_NAME}": i.metadata.name,
                       "{#INGRESS_NAMESPACE}": i.metadata.namespace,
                       "{#INGRESS_HOST}": i.spec.rules[0].host,
                       "{#INGRESS_STATUS_CODE_THRESHOLD}": v,
                       "{#INGRESS_STATUS_CODE}": k
                       }
                data['data'].append(tmp)

        print(json.dumps(data, sort_keys=True, indent=4))
    except ApiException as e:
        print("Exception when calling ExtensionsV1beta1Api->list_ingress_for_all_namespaces: %s\n" % e)


def discover_hpa():
    data = {"data": []}
    new_client = client.ApiClient(conf)
    api_instance = client.AutoscalingV1Api(new_client)
    try:
        api_response = api_instance.list_horizontal_pod_autoscaler_for_all_namespaces(watch=False)
        # pprint(api_response)
        for i in api_response.items:
            # print("%s\t%s\t%s" % (i.metadata.name, i.spec.scale_target_ref.name, i.status.current_replicas))
            tmp = {"{#HPA_NAME}": i.metadata.name,
                   "{#HPA_NAMESPACE}": i.metadata.namespace,
                   "{#HPA_TARGET_NAME}": i.spec.scale_target_ref.name,
                   "{#HPA_MIN_POD}": i.spec.min_replicas,
                   "{#HPA_MAX_POD}": i.spec.max_replicas,
                   "{#HPA_TARGET_CPU_PERCENTAGE}": i.spec.target_cpu_utilization_percentage
                   }
            data['data'].append(tmp)
        print(json.dumps(data, sort_keys=True, indent=4))
    except ApiException as e:
        print("Exception when calling AutoscalingV1Api->list_horizontal_pod_autoscaler_for_all_namespaces: %s\n" % e)


def discover_pods():
    data = {"data": []}
    new_client = client.ApiClient(conf)
    api_instance = client.CoreV1Api(new_client)
    try:
        api_response = api_instance.list_pod_for_all_namespaces(watch=False)
        # pprint(api_response)
        for i in api_response.items:
            tmp = {"{#POD_NAME}": i.metadata.name,
                   "{#POD_NAMESPACE}": i.metadata.namespace
                   }
            data['data'].append(tmp)
        print(json.dumps(data, sort_keys=True, indent=4))
    except ApiException as e:
        print("Exception when calling CoreV1Api->list_pod_for_all_namespaces: %s\n" % e)


def get_hpa_status(namespace, name, item):
    new_client = client.ApiClient(conf)
    api_instance = client.AutoscalingV1Api(new_client)
    try:
        api_response = api_instance.read_namespaced_horizontal_pod_autoscaler(name, namespace)
        if item == "current_replicas":
            print(api_response.status.current_replicas)

        if item == "desired_replicas":
            print(api_response.status.desired_replicas)

        if item == "current_cpu_utilization_percentage":
            print(api_response.status.current_cpu_utilization_percentage)

        # pprint(api_response)
    except ApiException as e:
        print("Exception when calling AutoscalingV1Api->read_namespaced_horizontal_pod_autoscaler: %s\n" % e)


def get_pod_status(namespace, name, item):
    new_client = client.ApiClient(conf)
    api_instance = client.CoreV1Api(new_client)
    try:
        api_response = api_instance.read_namespaced_pod(name, namespace)

        if item == "host_ip":
            print(api_response.status.host_ip)

        if item == "pod_ip":
            print(api_response.status.pod_ip)

        if item == "phase":
            print(api_response.status.phase)

        if item == "restart_count":
            print(api_response.status.container_statuses[0].restart_count)

        # pprint(api_response)
    except ApiException as e:
        print("Exception when calling CoreV1Api->read_namespaced_pod: %s\n" % e)


def cmd_line_opts(arg=None):
    """
    生成命令行选项
    :return:
    """

    class ParseHelpFormat(argparse.HelpFormatter):
        def __init__(self, prog, indent_increment=5, max_help_position=50, width=200):
            super(ParseHelpFormat, self).__init__(prog, indent_increment, max_help_position, width)

    parse = argparse.ArgumentParser(description='功能：k8s 系统hpa,ingress,pod zabbix监控脚本:',
                                    formatter_class=ParseHelpFormat)
    parse.add_argument('--version', '-v', action='version', version="1.0", help='查看版本')

    parse.add_argument('--discover-ingress', action='store_true', help='自动发现k8s ingress')
    parse.add_argument('--discover-hpa', action='store_true', help='自动发现k8s hpa')
    parse.add_argument('--discover-pods', action='store_true', help='自动发现k8s pods')
    parse.add_argument('--discover-nodes', action='store_true', help='自动发现k8s nodes')
    parse.add_argument('--namespace', help='命名空间')

    parse.add_argument('--get-hpa-cpu-utilization', action='store_true', help='获取HPA当前平均CPU')
    parse.add_argument('--get-hpa-desired-replicas', action='store_true', help='获取HPA当前希望得到的pod个数')
    parse.add_argument('--get-hpa-current-replicas', action='store_true', help='获取HPA当前运行的pod个数')
    parse.add_argument('--hpa-name', help='hpa 名称')

    parse.add_argument('--get-pod-host-ip', action='store_true', help='获取POD当前所在HOST节点IP')
    parse.add_argument('--get-pod-ip', action='store_true', help='获取POD当前IP')
    parse.add_argument('--get-pod-status', action='store_true', help='获取POD当前状态')
    parse.add_argument('--get-pod-restart-count', action='store_true', help='获取POD当前重启次数')
    parse.add_argument('--pod-name', help='pod 名称')

    parse.add_argument('--get-ingress-status-code-ratio', action='store_true', help='获取INGRESS 状态码占比')
    parse.add_argument('--domain',  help='INGRESS 域名')
    parse.add_argument('--status-code', help='INGRESS 状态码')

    if arg:
        return parse.parse_args(arg)
    if not sys.argv[1:]:
        return parse.parse_args(['-h'])
    else:
        return parse.parse_args()


if __name__ == '__main__':
    opts = cmd_line_opts()
    # 自动发现k8s ingress
    if opts.discover_ingress:
        discover_ingress()
    # k8s hpa 自动发现
    elif opts.discover_hpa:
        discover_hpa()
    # k8s pods 自动发现
    elif opts.discover_pods:
        discover_pods()
    # 获取k8s hpa的当前pod个数
    elif opts.namespace and opts.hpa_name and opts.get_hpa_current_replicas:
        get_hpa_status(opts.namespace, opts.hpa_name, "current_replicas")
    # 获取k8s hpa的当前希望得到的pod个数
    elif opts.namespace and opts.hpa_name and opts.get_hpa_desired_replicas:
        get_hpa_status(opts.namespace, opts.hpa_name, "desired_replicas")
    # 获取k8s hpa的当前pod平均CPU
    elif opts.namespace and opts.hpa_name and opts.get_hpa_cpu_utilization:
        get_hpa_status(opts.namespace, opts.hpa_name, "current_cpu_utilization_percentage")
    # 获取POD当前所在HOST节点IP
    elif opts.namespace and opts.pod_name and opts.get_pod_host_ip:
        get_pod_status(opts.namespace, opts.pod_name, "host_ip")
    # 获取POD当前IP
    elif opts.namespace and opts.pod_name and opts.get_pod_ip:
        get_pod_status(opts.namespace, opts.pod_name, "pod_ip")
    # 获取POD当前状态
    elif opts.namespace and opts.pod_name and opts.get_pod_status:
        get_pod_status(opts.namespace, opts.pod_name, "phase")
    # 获取POD当前重启次数
    elif opts.namespace and opts.pod_name and opts.get_pod_restart_count:
        get_pod_status(opts.namespace, opts.pod_name, "restart_count")
    # 获取ingress状态码百分比
    elif opts.get_ingress_status_code_ratio and opts.status_code and opts.domain:
        get_ingress_status(opts.domain, opts.status_code)
    # 帮助
    else:
        cmd_line_opts(arg=['-h'])
