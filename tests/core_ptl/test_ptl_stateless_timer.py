# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import shutil

import pytest
import torch
from omegaconf import OmegaConf
from pytorch_lightning import Trainer
from pytorch_lightning.utilities.distributed import rank_zero_only

from nemo.core import ModelPT
from nemo.utils import logging
from nemo.utils.exp_manager import CallbackParams, ExpManagerConfig, StatelessTimer, exp_manager


class OnesDataset(torch.utils.data.Dataset):
    def __init__(self, dataset_len):
        super().__init__()
        self.__dataset_len = dataset_len

    def __getitem__(self, *args):
        return torch.ones(2)

    def __len__(self):
        return self.__dataset_len


class ExampleModel(ModelPT):
    def __init__(self, *args, **kwargs):
        cfg = OmegaConf.structured({})
        super().__init__(cfg, trainer=kwargs.get('trainer', None))
        # dummy parameter in order to allow DDP to execute
        self.l1 = torch.nn.modules.Linear(in_features=2, out_features=1)

    def train_dataloader(self):
        dataset = OnesDataset(10000)
        return torch.utils.data.DataLoader(dataset, batch_size=2)

    def val_dataloader(self):
        dataset = OnesDataset(10)
        return torch.utils.data.DataLoader(dataset, batch_size=2)

    def predict_dataloader(self):
        dataset = OnesDataset(10)
        return torch.utils.data.DataLoader(dataset, batch_size=2)

    def forward(self, batch):
        return (self.l1(batch) - batch.mean(dim=1)).mean()

    def validation_step(self, batch, batch_idx):
        return (self.l1(batch) - batch.mean(dim=1)).mean()

    def training_step(self, batch, batch_idx):
        return (self.l1(batch) - batch.mean(dim=1)).mean()

    def list_available_models(self):
        pass

    def setup_training_data(self):
        pass

    def setup_validation_data(self):
        pass

    def validation_epoch_end(self, loss):
        if not loss:
            return
        self.log("val_loss", torch.stack(loss).mean())


class TestStatelessTimer:
    def setup_model(self):
        # Stateless timer for 3 seconds.
        # Max steps shouldn't matter for it should stop in 3 seconds based on the timer.
        # Val check interval makes sure a checkpoint is written and can be restored from.
        callback_params = CallbackParams()
        callback_params.monitor = "val_loss"
        callback_params.save_top_k = 1
        trainer = Trainer(
            devices=1,
            val_check_interval=5,
            max_steps=10000,
            accelerator='gpu',
            strategy='ddp',
            logger=None,
            enable_checkpointing=False,
            callbacks=[StatelessTimer('00:00:00:03')],
        )
        exp_manager_cfg = ExpManagerConfig(
            explicit_log_dir='./ptl_stateless_timer_check/',
            use_datetime_version=False,
            version="",
            resume_ignore_no_checkpoint=True,
            create_checkpoint_callback=True,
            checkpoint_callback_params=callback_params,
            resume_if_exists=True,
        )
        exp_manager(trainer, cfg=OmegaConf.structured(exp_manager_cfg))
        model = ExampleModel(trainer=trainer)
        trainer.fit(model)
        return trainer

    def cleanup(self):
        if os.path.exists('./ptl_stateless_timer_check'):
            shutil.rmtree('./ptl_stateless_timer_check', ignore_errors=True)

    @pytest.mark.run_only_on('GPU')
    @pytest.mark.unit
    def test_stateless_timer(self):
        self.cleanup()
        trainer = self.setup_model()
        global_step_1 = trainer.global_step
        trainer = self.setup_model()
        global_step_2 = trainer.global_step
        trainer = self.setup_model()
        global_step_3 = trainer.global_step
        logging.info(f"Global steps : {global_step_1}, {global_step_2}, {global_step_3}")
        assert global_step_3 > global_step_2 > global_step_1
        self.cleanup()
