export MAX_LENGTH=128
export BERT_MODEL=../models/netbert  #bert-base-cased

export OUTPUT_DIR=./output/finetuned_netbert  #./output/finetuned_basebert
export DATA_DIR=/raid/antoloui/Master-thesis/Data/NER
export CACHE_DIR=../cache
export BATCH_SIZE=32
export NUM_EPOCHS=3
export SAVE_STEPS=1000
export LIMIT_SAVE=10
export SEED=42


python run_ner.py \
    --model_type bert \
    --model_name_or_path $BERT_MODEL \
    --data_dir $DATA_DIR \
    --labels $DATA_DIR/labels.txt \
    --output_dir $OUTPUT_DIR \
    --cache_dir $CACHE_DIR \
    --max_seq_length $MAX_LENGTH \
    --num_train_epochs $NUM_EPOCHS \
    --per_gpu_train_batch_size $BATCH_SIZE \
    --save_steps $SAVE_STEPS \
    --save_total_limit $LIMIT_SAVE \
    --seed $SEED \
    --fp16 \
    --do_train \
    --do_eval |& tee ./output/logs/finetuned_netbert_logs.txt