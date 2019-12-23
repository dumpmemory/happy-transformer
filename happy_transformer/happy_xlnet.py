# pylint: disable=W0511
from transformers import XLNetLMHeadModel, XLNetTokenizer

from happy_transformer.happy_transformer import HappyTransformer
from happy_transformer.sequence_classification import SequenceClassifier
from happy_transformer.classifier_utils import classifier_args


class HappyXLNET(HappyTransformer):
    """
    Implementation of XLNET for masked word prediction
    """

    def __init__(self, model='xlnet-large-cased', initial_transformers=[]):
        super().__init__(model, initial_transformers)
        self.mlm = None
        self.seq = None
        self.model_version = model
        self.tokenizer = XLNetTokenizer.from_pretrained(model)
        self.masked_token = self.tokenizer.mask_token
        self.sep_token = self.tokenizer.sep_token
        self.cls_token = self.tokenizer.cls_token
        self.model = 'XLNET'
        self.classifier_name = ""



        self.seq_args = classifier_args.copy()
        self.seq_trained = False


    def _get_masked_language_model(self):
        """
        Initializes the XLNetLMHeadModel transformer
        """
        self.mlm = XLNetLMHeadModel.from_pretrained(self.model_to_use)
        self.mlm.eval()


    def _init_sequence_classifier(self, classifier_name: str):
        self.classifier_name = classifier_name
        self.seq_args['classifier_name'] = classifier_name
        self.seq_args['model_name'] = classifier_name
        self.seq_args['model_name'] = self.model_version
        self.seq_args['output_dir'] = "outputs/" + classifier_name
        self.seq_args['cache_dir'] = "cache/" + classifier_name
        self.seq_args['data_dir'] = "data/" + classifier_name
        self.seq = SequenceClassifier(self.seq_args)
        print(self.classifier_name, "has been initialized")


    def _train_sequence_classifier(self, train_df):
        if self.seq == None:
            print("First initialize the sequence classifier")
            return
        data_path = "data/" + self.classifier_name + "/train.tsv"
        train_df.to_csv(data_path, sep='\t', index=False, header=False, columns=train_df.columns)
        self.seq_args["do_train"] = True
        self.seq.run_sequence_classifier()
        self.seq_args["do_train"] = False
        self.seq_trained = True

        print("Training for ", self.classifier_name, "has been completed")


    def _test_sequence_classifier(self, test_df):
        if self.seq_trained == False:
            print("First train the sequence classifier")
            return

        data_path = "data/" + self.classifier_name + "/dev.tsv"

        test_df.to_csv(data_path, sep='\t', index=False, header=False, columns=test_df.columns)

        self.seq_args["do_eval"] = True
        self.seq.run_sequence_classifier()
        self.seq_args["do_eval"] = False
        print("Testing for ", self.classifier_name, "has been completed")




