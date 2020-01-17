# -*- coding: utf-8 -*-
# @Time    : 2019/8/24 12:06
# @Author  : zhoujun

import os
import sys

project = 'DBNet.pytorch'  # 工作项目根目录
sys.path.append(os.getcwd().split(project)[0] + project)
import time
import math
import cv2
import torch

from data_loader import get_transforms
from models import get_model
from post_processing import get_post_processing


def resize_image(img, short_size):
    height, width, _ = img.shape
    if height < width:
        new_height = short_size
        new_width = int(math.ceil(new_height / height * width / 32) * 32)
    else:
        new_width = short_size
        new_height = int(math.ceil(new_width / width * height / 32) * 32)
    resized_img = cv2.resize(img, (new_width, new_height))
    return resized_img


class Pytorch_model:
    def __init__(self, model_path, gpu_id=None):
        '''
        初始化pytorch模型
        :param model_path: 模型地址(可以是模型的参数或者参数和计算图一起保存的文件)
        :param gpu_id: 在哪一块gpu上运行
        '''
        self.gpu_id = gpu_id

        if self.gpu_id is not None and isinstance(self.gpu_id, int) and torch.cuda.is_available():
            self.device = torch.device("cuda:%s" % self.gpu_id)
        else:
            self.device = torch.device("cpu")
        print('device:', self.device)
        checkpoint = torch.load(model_path, map_location=self.device)

        config = checkpoint['config']
        config['arch']['args']['pretrained'] = False
        self.model = get_model(config['arch'])
        self.post_process = get_post_processing(config['post_processing'])
        self.img_mode = config['dataset']['train']['dataset']['args']['img_mode']
        self.model.load_state_dict(checkpoint['state_dict'])
        self.model.to(self.device)
        self.model.eval()

        self.transform = []
        for t in config['dataset']['train']['dataset']['args']['transforms']:
            if t['type'] in ['ToTensor', 'Normalize']:
                self.transform.append(t)
        self.transform = get_transforms(self.transform)

    def predict(self, img_path: str, is_output_polygon=False, short_size: int = 736):
        '''
        对传入的图像进行预测，支持图像地址,opecv 读取图片，偏慢
        :param img_path: 图像地址
        :param is_numpy:
        :return:
        '''
        assert os.path.exists(img_path), 'file is not exists'
        img = cv2.imread(img_path, 1 if self.img_mode != 'GRAY' else 0)
        if self.img_mode == 'RGB':
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        img = resize_image(img, short_size)
        # 将图片由(w,h)变为(1,img_channel,h,w)
        tensor = self.transform(img)
        tensor = tensor.unsqueeze_(0)

        tensor = tensor.to(self.device)
        batch = {'shape': [(h, w)]}
        with torch.no_grad():
            if str(self.device).__contains__('cuda'):
                torch.cuda.synchronize(self.device)
            start = time.time()
            preds = self.model(tensor)
            if str(self.device).__contains__('cuda'):
                torch.cuda.synchronize(self.device)
            box_list, score_list = self.post_process(batch, preds, is_output_polygon=is_output_polygon)
            box_list, score_list = box_list[0], score_list[0]
            if len(box_list) > 0:
                if is_output_polygon:
                    idx = [x.sum() > 0 for x in box_list]
                    box_list = [box_list[i] for i, v in enumerate(idx) if v]
                    score_list = [score_list[i] for i, v in enumerate(idx) if v]
                else:
                    idx = box_list.reshape(box_list.shape[0], -1).sum(axis=1) > 0  # 去掉全为0的框
                    box_list, score_list = box_list[idx], score_list[idx]
            else:
                box_list, score_list = [], []
            t = time.time() - start
        return preds[0, 0, :, :].detach().cpu().numpy(), box_list, score_list, t


def init_args():
    import argparse
    parser = argparse.ArgumentParser(description='DBNet.pytorch')
    parser.add_argument('--model_path', default='model_best.pth', type=str)
    parser.add_argument('--input_folder', default='./input', type=str, help='img path for predict')
    parser.add_argument('--output_folder', default='./input', type=str, help='img path for predict')
    parser.add_argument('--polygon', action='store_true', help='output polygon or box')
    parser.add_argument('--show', action='store_true', help='show result')
    parser.add_argument('--save_resut', action='store_true', help='save box and score to txt file')
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    import pathlib
    import matplotlib.pyplot as plt
    from utils.util import show_img, draw_bbox, save_result

    args = init_args()
    print(args)
    os.environ['CUDA_VISIBLE_DEVICES'] = str('0')
    # 初始化网络
    model = Pytorch_model(args.model_path, gpu_id=0)
    img_folder = pathlib.Path(args.input_folder)
    for img_path in img_folder.rglob('*.jpg'):
        preds, boxes_list, score_list, t = model.predict(str(img_path), is_output_polygon=args.polygon)
        img = draw_bbox(cv2.imread(str(img_path))[:, :, ::-1], boxes_list)
        if args.show:
            show_img(preds)
            show_img(img, title=img_path.name)
            plt.show()
        if args.save_resut:
            # 保存结果到路径
            os.makedirs(args.output_folder,exist_ok=True)
            output_path = os.path.join(args.output_folder, img_path.stem + '_result.jpg')
            cv2.imwrite(output_path, img[:, :, ::-1])
            save_result(output_path.replace('_result.jpg','txt'), boxes_list, score_list, args.polygon)
