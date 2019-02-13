# k8s_zabbix说明
k8s_zabbix实现了使用zabbix监控kubernetes的ingress,hpa,pod状态等功能。

Template Check K8S Cluster Status.xml：zabbix模板，可通过此文件导入到zabbix

check_k8s_status.py：kubernetes的监控脚本

userparameter_k8s.conf：zabbix agent端的配置文件，需要注意脚本的路径

## check_k8s_status.py说明

* 监控的k8s集群配置：
```python
conf.host = "https://10.10.88.20:8443"
conf.api_key['authorization'] = "xxxxxx.xxxxxxx.x-x-xxx-xxx-x"
```
* 脚本会监控traefik ingress的访问状态，将对400~599的非正常状态进行报警，需事先将traefik的访问日志通过fluentd或filebeat等导入到elasticsearch集群，脚本将定时通过查询访问日志来监控ingress的访问状态。监控脚本内的配置：
```python
# elasticsearch server config
es_server = [{"host": "10.16.252.50", "port": 9200},
             {"host": "10.16.252.50", "port": 9200},
             {"host": "10.16.252.50", "port": 9200}
             ]
# 索引名
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
```
