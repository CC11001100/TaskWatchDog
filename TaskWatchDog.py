#! /usr/bin/python

"""
TaskWatchDog（运维工具之消息推送工具）

    作者设想的应用场景：
        1. Linux下经常会运行一些任务，但是我们不可能一直在旁边看着吧，所以使用这个可以实现通告微信报告任务进度
        2. 配置文件和消息文件必须使用UTF8编码


"""


import time
import logging
import itchat
import json
import threading
import os
import sys
import argparse
from json import JSONDecodeError
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class MyFileSystemEventHandler(FileSystemEventHandler):
    """
    用于监控本地文件夹

        对要监控的文件夹，每次内容改变之后都会将改变之后的文本内容发送给指定的人
    """

    def on_created(self, event):
        logging.info('on_created event %s' % event.src_path)
        MyFileSystemEventHandler.process_new_msg(event.src_path)

    def on_modified(self, event):
        # logging.info('on_modified event %s' % event.src_path)
        # MyFileSystemEventHandler.process_new_msg(event.src_path)
        pass

    @staticmethod
    def process_new_msg(path):
        """
        这个方法用来处理一条新消息，将其内容读取并传递给发送者
        :param path:
        :return:
        """
        if not path.endswith('msg.txt'):
            return
        try:
            # 读取新消息的内容
            with open(path, encoding='UTF-8') as new_msg_file:
                # 直接在读取消息的时候使用最大长度限制，这样会比后面再截取好一些
                new_msg_content = new_msg_file.read(configuration['message_content_max_length'])
                logging.info('new message is: %s' % new_msg_content)
                message_sender.send_notification(new_msg_content)

            # 如果配置了移除旧消息的话，读取完之后就删除掉它，可能会抛出文件被其他应用程序占用无法移除的异常
            if configuration['remove_old_msg']:
                try:
                    os.remove(path)
                except PermissionError as e:
                    logging.error(e)
        except FileNotFoundError:
            pass
        except UnicodeDecodeError as e:
            logging.warning('message content is not utf-8: %s' % e)
        except Exception as e:
            logging.warning(e)


class WeiXinStatusCheck:
    """
    用于检查微信的登录状态，比如掉线的时候就退出啥的
    """
    @staticmethod
    def watch_weixin_status():
        while True:
            try:
                weixin_status = itchat.check_login()
                if weixin_status != '200':
                    logging.error('Wei Xin status exception %s' % weixin_status)
                    sys.exit(-1)
                else:
                    time.sleep(60*10) # 每十分钟检查一次登录状态，差不多就得了太频繁了小心被封
            except Exception as e:
                logging.error(e)
                sys.exit(-1)

class WatchDog:
    """
    用于目录监控

        这个类是用来监控某个目录发生变化的时候调用事件的
    """
    @staticmethod
    def watch(watch_path):
        event_handler = MyFileSystemEventHandler()
        observer = Observer()
        observer.schedule(event_handler, watch_path, recursive=True)
        observer.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()


class MessageSender:
    """
    发送微信消息的类

        这个类是用来发送微信消息的
    """

    # 存储要通知到的好友
    notice_friends = []

    def __init__(self):
        self.init_itchat()
        self.init_notification_friends()

    # 初始化微信相关的，这里会有一个需要登录的地方，登录的时候会阻塞住
    def init_itchat(self):
        try:
            itchat.auto_login(hotReload=True, enableCmdQR=configuration['use_qrcode'])    # 没有GUI界面的情况下应该将这个置为True
            logging.info('WeiXin login success.')
        except ExpatError as e:
            logging.error('Wei Xin status exception, open "https://wx2.qq.com/" try login for check.')
            sys.exit(-1)
        except Exception:
            logging.error(e)
            sys.exit(-1)

        # 这里只是为了hold住会话不超时即可，所以启动一个线程让它一直阻塞着即可
        t1 = threading.Thread(target=itchat.run)
        t1.setDaemon(True)
        t1.start()

        # 启动一个线程一直监控着微信的状态
        t2 = threading.Thread(target=WeiXinStatusCheck.watch_weixin_status)
        t2.setDaemon(True)
        t2.start()
        logging.info('')

    # 初始化应该通知到的好友
    def init_notification_friends(self):
        for friend in configuration['notice_friends']:
            match_friends = itchat.search_friends(remarkName=friend['remark_name'])
            if len(match_friends) == 0:
                logging.warning('No one friend remark match: %s, so ignore.' % friend['remark_name'])
                continue
            self.notice_friends.append(match_friends[0])
        if len(self.notice_friends) == 0:
            logging.info('Don\'t notice friends, because no one friend remark can match.')

    # 给每一个人发送通知
    def send_notification(self, msg_content):

        # 通知好友
        for friend in self.notice_friends:
            try:
                friend.send(msg_content)
                logging.info('Notice friend: %s <-- %s' %(friend['RemarkName'], msg_content))
            except Exception as e:
                logging.warning('then send message to the friend %s occur a exception %s' %(friend['RemarkName'], e))

        # 通知到文件助手
        if configuration['notice_filehelper']:
            try:
                itchat.send(msg_content, 'filehelper')
                logging.info('Notice filehelper<--%s' % msg_content)
            except Exception as e:
                logging.warning('then send message to filehelper occur a exception %s' % e)


class ConfigurationLoader:

    """
    用来加载配置文件，暂时的配置只支持JSON格式的
    """

    @staticmethod
    def load(config_location):
        """
        读取配置文件到内存中，并以JSON格式返回
        :param config_location:
        :return:
        """
        try:
            with open(config_location, encoding='UTF-8') as config_file:
                config = json.load(config_file)
                logging.info('read configuration %s is: %s' %(config_location, json.dumps(config)))
                return ConfigurationLoader.check_config(config)
        except FileNotFoundError:
            logging.error('Error: config file is not found, please ensure that file "%s" is exists.' % config_location)
            sys.exit(-1)
        except JSONDecodeError as e:
            logging.error('Error: config file is not a valid json file, error message is : %s' % e)
            sys.exit(-1)
        except Exception as e:
            logging.error('Error: %s' % e)
            sys.exit(-1)

    @staticmethod
    def check_config(config):
        """
        对配置文件进行合法性校验
        :param config:
        :return:
        """
        try:
            config['watch_path']
            config['notice_filehelper']

            for friend in config['notice_friends']:
                friend['remark_name']

            config['remove_old_msg']
            config['message_content_max_length']
            config['use_qrcode']
            return config
        except KeyError as e:
            logging.error('Error: in config file the key "%s" is not found or incorrect.' % e)
            sys.exit(-1)


if __name__ == "__main__":

    # 解析命令行参数
    parse = argparse.ArgumentParser(description=u'参数解析器')
    parse.add_argument('--config', type=str, default='./config.json')
    args = parse.parse_args()
    config_path = args.config

    # 日志相关配置
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s]: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # 读取配置文件
    configuration = ConfigurationLoader.load(config_path)

    # 初始化消息相关（微信通信）
    message_sender = MessageSender()

    # 监视某个文件夹
    # WatchDog.watch(configuration['watch_path'])
    t1 = threading.Thread(target=WatchDog.watch, args={configuration['watch_path']})
    t1.setDaemon(True)
    t1.start()

    logging.info('start success, waiting new message coming...')

    t1.join()


