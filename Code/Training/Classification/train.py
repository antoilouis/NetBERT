import json
import argparse
import sys
import time
import datetime
import random
import os

import pandas as pd
import numpy as np
import torch
from torch.utils.data import TensorDataset, DataLoader, RandomSampler, SequentialSampler

from keras.preprocessing.sequence import pad_sequences
from sklearn.model_selection import train_test_split

from transformers import BertTokenizer, BertForSequenceClassification, BertConfig
from transformers import AdamW, get_linear_schedule_with_warmup

try:
    from torch.utils.tensorboard import SummaryWriter
except ImportError:
    from tensorboardX import SummaryWriter


def parse_arguments():
    """
    Parser.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--filepath",
                        default='/raid/antoloui/Master-thesis/Data/Classification/cam_query_to_doctype.csv',
                        type=str,
                        #required=True,
                        help="Path of the file containing the sentences to encode.",
    )
    parser.add_argument("--model_name_or_path",
                        default= "../models/netbert/checkpoint-830000/", #'bert-base-cased',
                        type=str,
                        #required=True,
                        help="Path to pre-trained model or shortcut name",
    )
    parser.add_argument("--max_seq_length",
                        default=192,
                        type=int,
                        help="The maximum total input sequence length after tokenization. Sequences longer than this will be truncated, sequences shorter will be padded.",
    )
    parser.add_argument('--test_percent',
                        default=0.1,
                        type=float,
                        help='Percentage of available data to use for val/test dataset ([0,1]).',
    )
    parser.add_argument("--seed",
                        default=42,
                        type=int,
                        help="Random seed for initialization.",
    )
    parser.add_argument("--batch_size",
                        default=32, 
                        type=int,
                        help="Batch size per GPU/CPU for training. For fine-tuning BERT on a specific task, the authors recommend a batch size of 16 or 32.",
    )
    parser.add_argument("--cache_dir",
                        default='../cache/',
                        type=str,
                        help="Where do you want to store the pre-trained models downloaded from s3.",
    )
    parser.add_argument("--num_epochs",
                        default=4,
                        type=int,
                        help="Total number of training epochs to perform. Authors recommend between 2 and 4",
    )
    parser.add_argument("--learning_rate",
                        default=5e-5,
                        type=float,
                        help="The initial learning rate for Adam.")
    parser.add_argument("--adam_epsilon",
                        default=1e-8,
                        type=float,
                        help="Epsilon for Adam optimizer.",
    )
    parser.add_argument("--output_dir",
                        default='./output/',
                        type=str,
                        help="The output directory where the model predictions and checkpoints will be written.",
    )
    parser.add_argument("--gpu_id",
                        default=0,
                        type=int,
                        help="Id of the GPU to use if multiple GPUs.",
    )
    arguments, _ = parser.parse_known_args()
    return arguments


def load_data(filepath):
    """
    Filepath must be a csv file with 2 columns:
    - First column is a set of sentences;
    - Second column are the labels (strings) associated to the sentences.
    
    NB:
    - The delimiter is a comma;
    - The csv file must have a header;
    - The first column is the index column;
    """
    # Load the dataset into a pandas dataframe.
    df = pd.read_csv(filepath, delimiter=',', index_col=0)
    
    # Rename columns.
    df.columns = ['Sentence', 'Class']

    # Add categories ids column.
    categories = df.Class.unique()  # Get the categories.
    df['Class_id'] = df.apply(lambda row: np.where(categories == row.Class)[0][0], axis=1)
    return df, categories


def tokenize_sentences(model, max_len, df):
    """
    Tokenize all sentences in dataset with BertTokenizer.
    """
    # Load the BERT tokenizer.
    print('Loading BertTokenizer...\n')
    tokenizer = BertTokenizer.from_pretrained(model, do_lower_case=False)
    
    # Tokenize each sentence of the dataset.
    print("Tokenizing each sentence of the dataset...\n")
    tokenized = df['Sentence'].apply((lambda x: tokenizer.encode(x, add_special_tokens=True)))
    
    # Pad and truncate our sequences so that they all have the same length, max_len.
    print('Padding/truncating all sentences to {} values...'.format(max_len))
    print('Padding token: "{:}", ID: {:}\n'.format(tokenizer.pad_token, tokenizer.pad_token_id))
    tokenized = pad_sequences(tokenized, maxlen=max_len, dtype="long", 
                              value=0, truncating="post", padding="post") # "post" indicates that we want to pad and truncate at the end of the sequence, as opposed to the beginning.
    
    return tokenized, tokenizer


def create_masks(tokenized):
    """
    Given a list of tokenized sentences, create the corresponding attention masks.
    - If a token ID is 0, then it's padding, set the mask to 0.
    - If a token ID is > 0, then it's a real token, set the mask to 1.
    """
    attention_masks = []
    for sent in tokenized:
        att_mask = [int(token_id > 0) for token_id in sent]
        attention_masks.append(att_mask)
    return attention_masks


def split_data(tokenized, labels, attention_masks, test_percent, seed):
    """
    Split dataset to train/test.
    """
    if test_percent < 0.0 or test_percent > 1.0:
        print("Error: '--test_percent' must be between [0,1].")
        sys.exit()
        
    # Use 90% for training and 10% for validation.
    train_inputs, validation_inputs, train_labels, validation_labels = train_test_split(tokenized, labels, 
                                                                random_state=seed, test_size=test_percent)
    # Do the same for the masks.
    train_masks, validation_masks, _, _ = train_test_split(attention_masks, labels,
                                                 random_state=seed, test_size=test_percent)

    # Convert all inputs and labels into torch tensors, the required datatype for our model.
    train_inputs = torch.tensor(train_inputs)
    validation_inputs = torch.tensor(validation_inputs)

    train_labels = torch.tensor(train_labels)
    validation_labels = torch.tensor(validation_labels)

    train_masks = torch.tensor(train_masks)
    validation_masks = torch.tensor(validation_masks)
    
    return train_inputs, validation_inputs, train_labels, validation_labels, train_masks, validation_masks
    

def create_dataloader(train_inputs, validation_inputs, train_labels, validation_labels, train_masks, validation_masks, batch_size):
    """
    """
    # Create the DataLoader for our training set.
    train_data = TensorDataset(train_inputs, train_masks, train_labels)
    train_sampler = RandomSampler(train_data)
    train_dataloader = DataLoader(train_data, sampler=train_sampler, batch_size=batch_size)

    # Create the DataLoader for our validation set.
    validation_data = TensorDataset(validation_inputs, validation_masks, validation_labels)
    validation_sampler = SequentialSampler(validation_data)
    validation_dataloader = DataLoader(validation_data, sampler=validation_sampler, batch_size=batch_size)
    
    return train_data, train_sampler, train_dataloader, validation_data, validation_sampler, validation_dataloader


def flat_accuracy(preds, labels):
    """
    Calculate the accuracy of our predictions vs labels
    """
    pred_flat = np.argmax(preds, axis=1).flatten()
    labels_flat = labels.flatten()
    return np.sum(pred_flat == labels_flat) / len(labels_flat)


def format_time(elapsed):
    '''
    Takes a time in seconds and returns a string hh:mm:ss
    '''
    # Round to the nearest second.
    elapsed_rounded = int(round((elapsed)))
    
    # Format as hh:mm:ss
    return str(datetime.timedelta(seconds=elapsed_rounded))


def set_seed(seed):
    """
    Set seed.
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def main(args):
    print("\n========================================")
    print('               Load data                ')
    print("========================================\n")
    df, categories = load_data(args.filepath)
    sentences = df.Sentence.values  # Get all sentences.
    labels = df.Class_id.values  # Get the associated labels.
    
    print('Number of training sentences: {:,}\n'.format(df.shape[0]))
    print('Number of doc types: {:,}'.format(len(categories)))
    for i, cat in enumerate(categories):
        print("  {} : {}".format(cat, i))
    
    print("\n========================================")
    print('            Tokenize sentences          ')
    print("========================================\n")
    tokenized, tokenizer = tokenize_sentences(args.model_name_or_path, args.max_seq_length, df)
    attention_masks = create_masks(tokenized)
    print('Max sentence length: {}\n'.format(max([len(sent) for sent in tokenized])))
    
    # Print an example of tokenization.
    print("Example:")
    print('  - Original sentence: ', sentences[0])
    print('  - Tokenized sentence: ', tokenizer.tokenize(sentences[0]))
    print('  - Token IDs: ', tokenizer.convert_tokens_to_ids(tokenizer.tokenize(sentences[0])))
    print('  - Token IDs with [CLS] and [SEP] tokens: ', tokenizer.encode(sentences[0], add_special_tokens=True))
    print('  - Token IDs after padding/truncating:', list(tokenized[0]))
    print("  - Attention masks: ", attention_masks[0])
    
    
    print("\n========================================")
    print('               Prepare data             ')
    print("========================================\n")
    print("Splitting dataset to train/test...\n")
    train_inputs, validation_inputs, train_labels, validation_labels, train_masks, validation_masks = split_data(tokenized, labels, attention_masks, args.test_percent, args.seed)
    
    print("Creating dataloader...\n")
    train_data, train_sampler, train_dataloader, validation_data, validation_sampler, validation_dataloader = create_dataloader(train_inputs, validation_inputs, train_labels, validation_labels, train_masks, validation_masks, args.batch_size)
    
    
    print("\n========================================")
    print('                 Training               ')
    print("========================================\n")
    # Create tensorboard summarywriter
    tb_writer = SummaryWriter()
    
    # Setup CUDA, GPU
    torch.cuda.set_device(args.gpu_id)
    if torch.cuda.is_available():    
        device = torch.device("cuda") # Tell PyTorch to use the GPU.
        print('GPU training available! GPU used: {} ({})\n'.format(torch.cuda.get_device_name(args.gpu_id), args.gpu_id))
    else:
        print('No GPU available, using the CPU instead.')
        device = torch.device("cpu")

    # Set the seed value all over the place to make this reproducible.
    set_seed(args.seed)
    
    print("Loading BertForSequenceClassification model...\n")
    # Load pretrained BERT model with a single linear classification layer on top. 
    model = BertForSequenceClassification.from_pretrained(
        args.model_name_or_path, # Use the 12-layer BERT model, with an cased vocab.
        num_labels = len(categories), # The number of output labels
        output_attentions = False, # Whether the model returns attentions weights.
        output_hidden_states = False, # Whether the model returns all hidden-states.
        cache_dir = args.cache_dir,
    )
    model.to(device)  # Tell pytorch to run this model on the GPU.
    
    print("Setting up Optimizer & Learning Rate Scheduler...\n")
    optimizer = AdamW(model.parameters(),
                  lr = args.learning_rate,
                  eps = args.adam_epsilon
                )
    total_steps = len(train_dataloader) * args.num_epochs
    scheduler = get_linear_schedule_with_warmup(optimizer, 
                                                num_warmup_steps = 0, # Default value in run_glue.py
                                                num_training_steps = total_steps)

    # Store the average loss after each epoch so we can plot them.
    loss_values = []

    # For each epoch...
    for epoch_i in range(0, args.num_epochs):

        # ========================================
        #               Training
        # ========================================

        # Perform one full pass over the training set.
        print('======== Epoch {:} / {:} ========'.format(epoch_i + 1, args.num_epochs))
        print('Training...')

        # Measure how long the training epoch takes.
        t0 = time.time()

        # Reset the total loss for this epoch.
        total_loss = 0

        # Put the model into training mode. Don't be mislead--the call to 
        # `train` just changes the *mode*, it doesn't *perform* the training.
        # `dropout` and `batchnorm` layers behave differently during training
        # vs. test (source: https://stackoverflow.com/questions/51433378/what-does-model-train-do-in-pytorch)
        model.train()

        # For each batch of training data...
        for step, batch in enumerate(train_dataloader):

            # Progress update every 40 batches.
            if step % 40 == 0 and not step == 0:
                # Calculate elapsed time in minutes.
                elapsed = format_time(time.time() - t0)

                # Report progress.
                print('  Batch {:>5,}  of  {:>5,}.    Elapsed: {:}.'.format(step, len(train_dataloader), elapsed))

            # Unpack this training batch from our dataloader. 
            #
            # As we unpack the batch, we'll also copy each tensor to the GPU using the 
            # `to` method.
            #
            # `batch` contains three pytorch tensors:
            #   [0]: input ids 
            #   [1]: attention masks
            #   [2]: labels 
            b_input_ids = batch[0].to(device)
            b_input_mask = batch[1].to(device)
            b_labels = batch[2].to(device)

            # Always clear any previously calculated gradients before performing a
            # backward pass. PyTorch doesn't do this automatically because 
            # accumulating the gradients is "convenient while training RNNs". 
            # (source: https://stackoverflow.com/questions/48001598/why-do-we-need-to-call-zero-grad-in-pytorch)
            model.zero_grad()        

            # Perform a forward pass (evaluate the model on this training batch).
            # This will return the loss (rather than the model output) because we
            # have provided the `labels`.
            # The documentation for this `model` function is here: 
            # https://huggingface.co/transformers/v2.2.0/model_doc/bert.html#transformers.BertForSequenceClassification
            outputs = model(b_input_ids, 
                        token_type_ids=None, 
                        attention_mask=b_input_mask, 
                        labels=b_labels)

            # The call to `model` always returns a tuple, so we need to pull the 
            # loss value out of the tuple.
            loss = outputs[0]

            # Accumulate the training loss over all of the batches so that we can
            # calculate the average loss at the end. `loss` is a Tensor containing a
            # single value; the `.item()` function just returns the Python value 
            # from the tensor.
            total_loss += loss.item()
            tb_writer.add_scalar('Loss', loss.item(), step)

            # Perform a backward pass to calculate the gradients.
            loss.backward()

            # Clip the norm of the gradients to 1.0.
            # This is to help prevent the "exploding gradients" problem.
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)

            # Update parameters and take a step using the computed gradient.
            # The optimizer dictates the "update rule"--how the parameters are
            # modified based on their gradients, the learning rate, etc.
            optimizer.step()

            # Update the learning rate.
            scheduler.step()

        # Calculate the average loss over the training data.
        avg_train_loss = total_loss / len(train_dataloader)            

        # Store the loss value for plotting the learning curve.
        loss_values.append(avg_train_loss)

        print("")
        print("  Average training loss: {0:.2f}".format(avg_train_loss))
        print("  Training epcoh took: {:}".format(format_time(time.time() - t0)))

        # ========================================
        #               Validation
        # ========================================
        # After the completion of each training epoch, measure our performance on
        # our validation set.

        print("")
        print("Running Validation...")

        t0 = time.time()

        # Put the model in evaluation mode--the dropout layers behave differently
        # during evaluation.
        model.eval()

        # Tracking variables 
        eval_loss, eval_accuracy = 0, 0
        nb_eval_steps, nb_eval_examples = 0, 0

        # Evaluate data for one epoch
        for batch in validation_dataloader:

            # Add batch to GPU
            batch = tuple(t.to(device) for t in batch)

            # Unpack the inputs from our dataloader
            b_input_ids, b_input_mask, b_labels = batch

            # Telling the model not to compute or store gradients, saving memory and
            # speeding up validation
            with torch.no_grad():        

                # Forward pass, calculate logit predictions.
                # This will return the logits rather than the loss because we have
                # not provided labels.
                # token_type_ids is the same as the "segment ids", which 
                # differentiates sentence 1 and 2 in 2-sentence tasks.
                # The documentation for this `model` function is here: 
                # https://huggingface.co/transformers/v2.2.0/model_doc/bert.html#transformers.BertForSequenceClassification
                outputs = model(b_input_ids, 
                                token_type_ids=None, 
                                attention_mask=b_input_mask)

            # Get the "logits" output by the model. The "logits" are the output
            # values prior to applying an activation function like the softmax.
            logits = outputs[0]

            # Move logits and labels to CPU
            logits = logits.detach().cpu().numpy()
            label_ids = b_labels.to('cpu').numpy()

            # Calculate the accuracy for this batch of test sentences.
            tmp_eval_accuracy = flat_accuracy(logits, label_ids)

            # Accumulate the total accuracy.
            eval_accuracy += tmp_eval_accuracy

            # Track the number of batches
            nb_eval_steps += 1

        # Report the final accuracy for this validation run.
        print("  Accuracy: {0:.2f}".format(eval_accuracy/nb_eval_steps))
        print("  Validation took: {:}".format(format_time(time.time() - t0)))

    print("\nTraining complete!\n")
    
    print("Saving model to %s...\n" % output_dir)
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    # Save a trained model, configuration and tokenizer using `save_pretrained()`.
    # They can then be reloaded using `from_pretrained()`
    model_to_save = model.module if hasattr(model, 'module') else model  # Take care of distributed/parallel training
    model_to_save.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)

    # Good practice: save your training arguments together with the trained model
    torch.save(args, os.path.join(output_dir, 'training_args.bin'))
    

if __name__=="__main__":
    args = parse_arguments()
    main(args)
