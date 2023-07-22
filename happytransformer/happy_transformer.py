import logging
import os
import tempfile
from typing import Union

from datasets import Dataset, DatasetDict,  load_dataset, load_from_disk
import torch
from transformers import (
    AutoConfig,
    AutoModel,
    AutoTokenizer,
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    Trainer,
    TrainingArguments
    )

from happytransformer.args import EvalArgs, TrainArgs
from happytransformer.fine_tuning_util import EvalResult

class HappyTransformer():

    def __init__(self, model_type: str, model_name: str, model_class: AutoModel, load_path="", use_auth_token: Union[str, bool] = None):

        self.logger = self._get_logger()
        self.model_type = model_type
        self.model_name = model_name
        self.use_auth_token = use_auth_token
        self._model_class = model_class

        # Sets self.model and self.tokenizer if load_model is True
        if load_path != "":
            self.config, self.tokenizer, self.model = self._get_model_components(load_path)
        else:
            self.config, self.tokenizer, self.model = self._get_model_components(self.model_name)

        self.device = self.to_auto_device()

        self.logger.info("Using device: %s", self.device)

        if self.tokenizer.pad_token is None and self.tokenizer.eos_token is not None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.model.resize_token_embeddings(len(self.tokenizer))

        # Set within the child classes.
        self._data_collator = None
        self._t_data_file_type = None
        self._type = None

    ######## Children of
    def _tok_function(self, raw_dataset, args: TrainArgs, format: str) -> Dataset:
        raise NotImplementedError()

    ######## Helper __init__ methods ########

    def _get_model_components(self, model_name_path):
        # HappyTextClassification is the only class that overwrites
        # this as we need to specify number of labels.

        config = AutoConfig.from_pretrained(model_name_path, use_auth_token=self.use_auth_token)
        model = self._model_class.from_pretrained(model_name_path, config=config, use_auth_token=self.use_auth_token)
        tokenizer = AutoTokenizer.from_pretrained(model_name_path, use_auth_token=self.use_auth_token)

        return config, tokenizer, model

    def _get_logger(self) :
        logger = logging.getLogger(__name__)
        handler = logging.StreamHandler()
        handler.addFilter(logging.Filter('happytransformer'))
        logging.basicConfig(
            format='%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
            datefmt='%m/%d/%Y %H:%M:%S',
            level=logging.INFO,
            handlers=[handler]
        )
        return logger


    def to_auto_device(self):
        device = None
        if torch.backends.mps.is_available():
            if torch.backends.mps.is_built():
                device = torch.device("mps")

        if torch.cuda.is_available():
            device = torch.device("cuda:0")

        if not device:
            device = torch.device("cpu")

        self.model.to(device)
        return device

    def train(self, input_filepath: str ,  args: TrainArgs, eval_filepath: str = "", ):
        if type(args) == dict:
            raise ValueError("Dictionary training arguments are no longer supported as of Happy Transformer version 2.5.0.")

        if args.eval_ratio <= 0 and eval_filepath == "":
            raise ValueError("Please set TrainArgs.eval_ratio to greater than 0  or supply an eval_path")

        train_tok_data, eval_tok_data = self._preprocess_data_train(input_filepath=input_filepath,
                                                              eval_filepath=eval_filepath,
                                                              args=args)

        self._run_train(train_tok_data, eval_tok_data, args,  self._data_collator)



    def eval(self, input_filepath, args):
        if type(args) == dict:
            raise ValueError(
                "Dictionary evaluating arguments are no longer supported as of Happy Transformer version 2.5.0.")


        tokenized_dataset = self._preprocess_data_eval(input_filepath, args)

        result = self._run_eval(tokenized_dataset, self._data_collator, args)

        print("RESULT", result)

        return EvalResult(loss=result["eval_loss"])

    def test(self, input_filepath, args):
        raise NotImplementedError()

    def save(self, path):
        self.model.save_pretrained(path)
        self.tokenizer.save_pretrained(path)

    ######## Data Preprocessing ########
    def _preprocess_data_train(self, input_filepath, eval_filepath, args: TrainArgs):

        if not args.load_preprocessed_data:
            # We are loading raw data
            if eval_filepath == "":

                # eval_filepath was not provided so we use a portion of the training data for evaluating
                file_type = self._check_file_type(input_filepath)
                all_raw_data = load_dataset(file_type, data_files={"train": input_filepath}, split="train")
                # Shuffle data
                all_raw_data = all_raw_data.shuffle(seed=42)
                # Split according to args.eval_ratio
                split_text_data = all_raw_data.train_test_split(test_size=args.eval_ratio)
                self.logger.info("Tokenizing training data...")

                train_tok_data = self._tok_function(split_text_data["train"], args, file_type)
                eval_tok_data = self._tok_function(split_text_data["test"], args, file_type)
            else:
                # Eval path has been provided so we can load the evaluating data directly.
                train_file_type = self._check_file_type(input_filepath)
                eval_file_type = self._check_file_type(input_filepath)

                if train_file_type != eval_file_type:
                    raise ValueError("Train file-type must be the same as the eval file-type")

                raw_data = load_dataset(train_file_type, data_files={"train": input_filepath, "eval": eval_filepath})

                # todo combine logic below with above tokenizing logic
                self.logger.info("Tokenizing training data...")
                train_tok_data = self._tok_function(raw_data["train"], args, train_file_type)
                self.logger.info("Tokenizing eval data...")
                eval_tok_data = self._tok_function(raw_data["eval"], args, train_file_type)
        else:
            if args.save_preprocessed_data_path.endswith(".json"):
                raise ValueError(
                    "As of version 2.5.0 preprocessed files are not longer saved as json files. Please preprocess your data again")

            if eval_filepath != "":
                self.logger.warning(f"Eval data will be fetched from {args.load_preprocessed_data_path} and not {eval_filepath}")

            tok_data = load_from_disk(args.load_preprocessed_data_path)
            train_tok_data = tok_data["train"]
            eval_tok_data = tok_data["eval"]

        if args.save_preprocessed_data:

            if args.save_preprocessed_data_path.endswith(".json"):
                raise ValueError(
                    "As of version 2.5.0 preprocessed files are not longer saved as json files. Please provide a path to a folder.")


            combined_tok = DatasetDict({"train": train_tok_data, "eval": eval_tok_data})
            combined_tok.save_to_disk(args.save_preprocessed_data_path)

        return train_tok_data, eval_tok_data


    def _preprocess_data_eval(self, input_filepath, args: TrainArgs):
        if not args.load_preprocessed_data:
            self.logger.info("Preprocessing dataset...")
            eval_file_type = self._check_file_type(input_filepath)
            datasets = load_dataset(eval_file_type, data_files={"eval": input_filepath})
            tokenized_dataset = self._tok_function(datasets["eval"], args, eval_file_type)

        else:
            self.logger.info("Loading dataset from %s...", args.load_preprocessed_data_path)
            tokenized_dataset = load_from_disk(args.load_preprocessed_data_path +"/eval")

        if args.save_preprocessed_data:
            if args.load_preprocessed_data:
                self.logger.warning("Both save_preprocessed_data and load_data are enabled.")

            self.logger.info("Saving evaluating dataset to %s...", args.save_preprocessed_data_path)
            save_dataset = DatasetDict({"eval": tokenized_dataset})
            save_dataset.save_to_disk(args.save_preprocessed_data_path)

        return tokenized_dataset



    def _get_training_args(self, args):
        if self.device.type != "cuda":
            if args.fp16:
                ValueError("fp16 is only available when CUDA/ a GPU is being used. ")

        if self._type == "tt":
            arg_class = Seq2SeqTrainingArguments
        else:
            arg_class = TrainingArguments

        return arg_class(
            deepspeed=None if args.deepspeed == "" else args.deepspeed,
            output_dir=args.output_dir,
            learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,
            adam_beta1=args.adam_beta1,
            adam_beta2=args.adam_beta2,
            adam_epsilon=args.adam_epsilon,
            max_grad_norm=args.max_grad_norm,
            num_train_epochs=args.num_train_epochs,
            report_to=["none"] if len(args.report_to) == 0 else args.report_to,
            save_strategy="steps" if args.save_steps > 0 else "no",
            save_steps=args.save_steps,
            evaluation_strategy="steps" if args.eval_steps > 0 else "no",
            eval_steps=args.eval_steps,
            logging_strategy="steps" if args.logging_steps > 0 else "no",
            logging_steps = args.logging_steps,
            per_device_train_batch_size=args.batch_size,
            fp16=args.fp16,
            gradient_accumulation_steps=args.gas,
            use_mps_device= True if self.device.type == "mps" else False,
            run_name=args.run_name,
        )


    def _run_train(self, train_dataset, eval_dataset, args, data_collator):
        """
        :param dataset: a child of torch.utils.data.Dataset
        :param args: a dataclass that contains settings
        :return: None
        """
        training_args = self._get_training_args(args)

        os.environ["WANDB_PROJECT"] = args.project_name

        if self._type == "wp":
            self._data_collator.mlm_probability = args.mlm_probability

        if self._type == "tt":
            train_class = Seq2SeqTrainer
        else:
            train_class = Trainer

        trainer = train_class(
            model=self.model,
            args=training_args,
            train_dataset=train_dataset,
            eval_dataset=eval_dataset,
            tokenizer=self.tokenizer,
            data_collator=data_collator,
        )
        trainer.train()

    def _run_eval(self, dataset, data_collator, args):
        if self._type == "wp":
            self._data_collator.mlm_probability = args.mlm_probability

        with tempfile.TemporaryDirectory() as tmp_dir_name:
            eval_args = self._get_eval_args(tmp_dir_name, args)
            trainer = Trainer(
                model=self.model,
                args=eval_args,
                eval_dataset=dataset,
                data_collator=data_collator
            )
            return trainer.evaluate()

    def _get_eval_args(self, output_path: str, args: EvalArgs) -> TrainingArguments:
        return TrainingArguments(
            output_dir=output_path,
            seed=42,
            report_to=['none'],
            per_device_eval_batch_size=args.batch_size,
            use_mps_device=True if self.device.type == "mps" else False,
            deepspeed=None if args.deepspeed == "" else args.deepspeed
        )


    def push_to_hub(self, repo_name, private=True):
        self.logger.info("Pushing model...")
        self.model.push_to_hub(repo_name, private=private)
        self.logger.info("Pushing tokenizer...")
        self.tokenizer.push_to_hub(repo_name, private=private)


    def _check_file_type(self, file_path):
        split_path = os.path.splitext(file_path)

        file_extension = split_path[1]

        # maps the suffix of the file-path to the name that Hugging Face's  load_dataset function uses
        ending_map = {".txt": "text", ".csv": "csv"}
        ending = ending_map[file_extension]

        if ending not in self._t_data_file_type:
            ValueError(f"Invalid file type for {file_path}.")

        return ending
