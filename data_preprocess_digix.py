from numpy.core.fromnumeric import shape
from dlrm_data_pytorch import collate_wrapper_criteo_length, collate_wrapper_criteo_offset
from os import path
import torch 
from torch.utils.data import Dataset, RandomSampler
import pandas as pd 
import numpy as np
from sklearn.preprocessing import OneHotEncoder
from imblearn.under_sampling import RandomUnderSampler



def collate_wrapper_digix_offset(list_of_tuples):
    # where each tuple is (X_int, X_cat, y)
    transposed_data = list(zip(*list_of_tuples))
   # transposed_data[1] = np.transpose(transposed_data[1])
    #X_int = torch.log(torch.tensor(transposed_data[0], dtype=torch.float) + 1)
    X_int = torch.tensor(transposed_data[0], dtype=torch.float)

    X_cat = torch.tensor(transposed_data[1], dtype=torch.long)
    T = torch.tensor(transposed_data[2], dtype=torch.float32).view(-1, 1)

    batchSize = X_cat.shape[0]
    featureCnt = X_cat.shape[1]

    lS_i = [X_cat[:, i] for i in range(featureCnt)]
    lS_o = [torch.tensor(range(batchSize)) for _ in range(featureCnt)]

    return X_int, torch.stack(lS_o), torch.stack(lS_i), T


def make_digix_data_and_loaders(args, offset_to_length_converter=False):

    train_data = DigixDataset(
        args.data_set,
        args.max_ind_range,
        args.data_sub_sample_rate,
        args.data_randomize,
        "train",
        args.raw_data_file,
        args.processed_data_file,
        args.memory_map,
        args.dataset_multiprocessing,
    )

    test_data = DigixDataset(
        args.data_set,
        args.max_ind_range,
        args.data_sub_sample_rate,
        args.data_randomize,
        "test",
        args.raw_data_file,
        args.processed_data_file,
        args.memory_map,
        args.dataset_multiprocessing,
    )

    collate_wrapper_criteo = collate_wrapper_digix_offset
    if offset_to_length_converter:
        collate_wrapper_criteo = collate_wrapper_criteo_length

    train_loader = torch.utils.data.DataLoader(
        train_data,
        batch_size=args.mini_batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        collate_fn=collate_wrapper_criteo,
        pin_memory=False,
        drop_last=False,  # True
    )

    test_loader = torch.utils.data.DataLoader(
        test_data,
        batch_size=args.test_mini_batch_size,
        shuffle=False,
        num_workers=args.test_num_workers,
        collate_fn=collate_wrapper_criteo,
        pin_memory=False,
        drop_last=False,  # True
    )

    return train_data, train_loader, test_data, test_loader

def getDigixData(
        datafile,
        o_filename,
        max_ind_range=-1,
        memory_map=False,
        dataset_multiprocessing=False,
    ):
    # read data 
    #df = pd.read_csv(datafile, sep="|")

    chunksize = 10 ** 6
    num_of_chunk = 0
    df = pd.DataFrame()
        
    for chunk in pd.read_csv(datafile, chunksize=chunksize, sep = ","):# sep="|"):
        num_of_chunk += 1

        uids = chunk[["uid","label"]]
        nulls = list(uids[uids["label"] == 0]["uid"])
        ones = list(uids[uids["label"] == 1]["uid"])
        toss_out = list(set(nulls) - set(ones))

        # also add everything that is already in train
        if num_of_chunk == 1:
        # keep_chunk = chunk[chunk["uid"].isin(keep)]
            keep_chunk = chunk[~chunk["uid"].isin(toss_out)]
        else: 
            toss_out = list(set(toss_out) - set(df["uid"].unique()))
            keep_chunk = chunk[~chunk["uid"].isin(toss_out)]

        if num_of_chunk == 20:
            break
        
        df = pd.concat([df, keep_chunk], axis=0)
        print('Processing Chunk No. ' + str(num_of_chunk))   
        print("df.shape is " + str(df.shape))  
        
    df.reset_index(inplace=True)
    print("loaded data frame of size {}".format(df.shape))

    np.random.seed(123)


    # now we must do some downsampling of the larger group in order to get meaningful trainingset
    rus = RandomUnderSampler(sampling_strategy=2/3, random_state=1)
    df_balanced, balanced_labels = rus.fit_resample(df, df['label'])
    df_balanced = pd.DataFrame(df_balanced, columns=df.columns)


    df = df_balanced
    print("shape after downsampling is now {}".format(df.shape))
    
    #reshuffle the data 
    df = df.iloc[np.random.permutation(len(df))]
    df = df.reset_index(drop = True)

    #select label, cont and cat as well as variables to delete
    y = "label"
    delete = ["communication_onlinerate", "pt_d"]
    cont = ["age", "device_size", "his_app_size", "list_time","device_price",
        "up_life_duration", "membership_life_duration","communication_avgonline_30d"]

    cat = list(set(list(df.columns)) - set(delete) -set(cont) - set([y]))
    df_cat = df[cat]
    #transfrom cont df and label series to np array
    np_cont = np.array(df[cont])
    np_y = np.array(df[y]) 


    # get number of embeddings and offsets (counts is number of different categories per embedding (rows per embedding matrix))
    # get index of each item in each cat column as if we were to call df[col].unique() and whichever item is first gets index 0 and so on
    overall_dic = dict()
    for column in df_cat.columns:
        uniques = df_cat[column].unique()
        overall_dic[column] = pd.DataFrame()
        overall_dic[column][column] = uniques
        overall_dic[column][column + "_index"] = np.arange(0,len(uniques))

    colu = df_cat.columns
    for col in colu:
        print(col)
        df_cat = pd.merge(df_cat, overall_dic[col], on=col, how='left')
        df_cat = df_cat.drop(col, axis = 1)

    counts = np.array([df_cat[col].nunique() for col in df_cat.columns])
    np_cat = np.array(df_cat)


    np.savez_compressed(
        o_filename,
        X_int=np_cont,
        X_cat = np_cat,
        #X_cat_t=np.transpose(X_cat[0:i, :]),  # transpose of the data
        y=np_y,
        counts = counts)
    
    print("\nSaved " + o_filename)


    return o_filename




class DigixDataset(Dataset):

    def _default_preprocess(self, X_int, X_cat, y):
        X_int = torch.log(torch.tensor(X_int, dtype=torch.float) + 1)
        if self.max_ind_range > 0:
            X_cat = torch.tensor(X_cat % self.max_ind_range, dtype=torch.long)
        else:
            X_cat = torch.tensor(X_cat, dtype=torch.long)
        y = torch.tensor(y.astype(np.float32))

        return X_int, X_cat, y

    def __len__(self):
        if self.memory_map:
            if self.split == 'none':
                return self.offset_per_file[-1]
            elif self.split == 'train':
                return self.offset_per_file[-2]
            elif self.split == 'test':
                return self.test_size
            elif self.split == 'val':
                return self.val_size
            else:
                sys.exit("ERROR: dataset split is neither none, nor train nor test.")
        else:
            return len(self.y)

    def __getitem__(self, index):

        if isinstance(index, slice):
            return [
                self[idx] for idx in range(
                    index.start or 0, index.stop or len(self), index.step or 1
                )
            ]

        if self.memory_map:
            if self.split == 'none' or self.split == 'train':
                # check if need to swicth to next day and load data
                if index == self.offset_per_file[self.day]:
                    # print("day_boundary switch", index)
                    self.day_boundary = self.offset_per_file[self.day]
                    fi = self.npzfile + "_{0}_reordered.npz".format(
                        self.day
                    )
                    # print('Loading file: ', fi)
                    with np.load(fi, allow_pickle=True) as data:
                        self.X_int = data["X_int"]  # continuous  feature
                        self.X_cat = data["X_cat"]  # categorical feature
                        self.y = data["y"]          # target
                    self.day = (self.day + 1) % self.max_day_range

                i = index - self.day_boundary
            elif self.split == 'test' or self.split == 'val':
                # only a single day is used for testing
                i = index + (0 if self.split == 'test' else self.test_size)
            else:
                sys.exit("ERROR: dataset split is neither none, nor train or test.")
        else:
            i = index

        if self.max_ind_range > 0:
            return self.X_int[i], self.X_cat[i] % self.max_ind_range, self.y[i]
        else:
            return self.X_int[i], self.X_cat[i], self.y[i]

    def __init__(
            self,
            dataset,
            max_ind_range,
            sub_sample_rate,
            randomize,
            split="train",
            raw_path="", # path of raw data
            pro_data="", # path of processed data
            memory_map=False,
            dataset_multiprocessing=False):
            
        # dataset
        # tar_fea = 1   # single target
        den_fea = 13  # 13 dense  features
        # spa_fea = 26  # 26 sparse features
        # tad_fea = tar_fea + den_fea
        # tot_fea = tad_fea + spa_fea
        if dataset == "digix":
            days = 1
            out_file = "digix_processed.npz"
        else:
            raise(ValueError("Data set option is not supported"))
        self.max_ind_range = max_ind_range
        self.memory_map = memory_map

        # split the datafile into path and filename
        lstr = raw_path.split("/")
        self.d_path = "/".join(lstr[0:-1]) + "/"
        self.d_file = lstr[-1].split(".")[0] if dataset == "kaggle" else lstr[-1]
        self.npzfile = self.d_path + (
            (self.d_file + "_day") if dataset == "kaggle" else self.d_file
        )
        self.trafile = self.d_path + (
            (self.d_file + "_fea") if dataset == "kaggle" else "fea"
        )

        # check if pre-processed data is available
        data_ready = True
        if memory_map:
            for i in range(days):
                reo_data = self.npzfile + "_{0}_reordered.npz".format(i)
                if not path.exists(str(reo_data)):
                    data_ready = False
        else:
            if not path.exists(str(pro_data)):
                data_ready = False

        # pre-process data if needed
        # WARNNING: when memory mapping is used we get a collection of files
        if data_ready:
            print("Reading pre-processed data=%s" % (str(pro_data)))
            file = str(pro_data)
        else:
            print("Reading raw data=%s" % (str(raw_path)))
            file = getDigixData(
                raw_path,
                out_file,
                max_ind_range,
                memory_map,
                dataset_multiprocessing,
            )

        # get a number of samples per day
#        total_file = self.d_path + self.d_file + "_day_count.npz"
#        with np.load(total_file) as data:
#            total_per_file = data["total_per_file"]
        # compute offsets per file
#        self.offset_per_file = np.array([0] + [x for x in total_per_file])
#        for i in range(days):
#            self.offset_per_file[i + 1] += self.offset_per_file[i]
        # print(self.offset_per_file)

        # setup data
        if memory_map:
            # setup the training/testing split
            self.split = split
            if split == 'none' or split == 'train':
                self.day = 0
                self.max_day_range = days if split == 'none' else days - 1
            elif split == 'test' or split == 'val':
                self.day = days - 1
                num_samples = self.offset_per_file[days] - \
                              self.offset_per_file[days - 1]
                self.test_size = int(np.ceil(num_samples / 2.))
                self.val_size = num_samples - self.test_size
            else:
                sys.exit("ERROR: dataset split is neither none, nor train or test.")

            # load unique counts
            with np.load(self.d_path + self.d_file + "_fea_count.npz") as data:
                self.counts = data["counts"]
            self.m_den = den_fea  # X_int.shape[1]
            self.n_emb = len(self.counts)
            print("Sparse features= %d, Dense features= %d" % (self.n_emb, self.m_den))

            # Load the test data
            # Only a single day is used for testing
            if self.split == 'test' or self.split == 'val':
                # only a single day is used for testing
                fi = self.npzfile + "_{0}_reordered.npz".format(
                    self.day
                )
                with np.load(fi, allow_pickle=True) as data:
                    self.X_int = data["X_int"]  # continuous  feature
                    self.X_cat = data["X_cat"]  # categorical feature
                    self.y = data["y"]          # target

        else:
            # load and preprocess data
            with np.load(file, allow_pickle=True) as data:
                X_int = data["X_int"]  # continuous  feature
                X_cat = data["X_cat"]  # categorical feature
                y = data["y"]          # target
                self.counts = data["counts"]
            self.m_den = X_int.shape[1]  # den_fea
            self.n_emb = len(self.counts)
            print("Sparse fea = %d, Dense fea = %d" % (self.n_emb, self.m_den))

            # create reordering
            indices = np.arange(len(y))

            if split == "none":
                # randomize all data
                if randomize == "total":
                    indices = np.random.permutation(indices)
                    print("Randomized indices...")

                X_int[indices] = X_int
                X_cat[indices] = X_cat
                y[indices] = y

            else:
                # split indices up into train test and validation
               # indices = np.random.permutation(indices)
                train_test_split = int(np.ceil(len(indices)*0.7))
                
                train_indices = indices[:train_test_split]
                test_indices = indices[train_test_split:]
                # split test indices further in test and validation
                #test_indices, val_indices = np.array_split(test_indices, 2)

                print("Defined %s indices..." % (split))

                # create training, validation, and test sets
                if split == 'train':
                    self.X_int = [X_int[i] for i in train_indices]
                    self.X_cat = [X_cat[i] for i in train_indices]
                    self.y = [y[i] for i in train_indices]
                elif split == 'val':
                    self.X_int = [X_int[i] for i in val_indices]
                    self.X_cat = [X_cat[i] for i in val_indices]
                    self.y = [y[i] for i in val_indices]
                elif split == 'test':
                    self.X_int = [X_int[i] for i in test_indices]
                    self.X_cat = [X_cat[i] for i in test_indices]
                    self.y = [y[i] for i in test_indices]

            print("Split data according to indices...")

            ### or do this
                # now do train test split and/or randomization
'''                indices = np.arange(len(y))

                    if randomize == "total":
                    train_indices = np.random.permutation(train_indices)
                    print("Randomized indices across days ...")

                # create training, validation, and test sets
                if split == 'train':
                    self.X_int = [X_int[i] for i in train_indices]
                    self.X_cat = [X_cat[i] for i in train_indices]
                    self.y = [y[i] for i in train_indices]
                elif split == 'val':
                    self.X_int = [X_int[i] for i in val_indices]
                    self.X_cat = [X_cat[i] for i in val_indices]
                    self.y = [y[i] for i in val_indices]
                elif split == 'test':
                    self.X_int = [X_int[i] for i in test_indices]
                    self.X_cat = [X_cat[i] for i in test_indices]
                    self.y = [y[i] for i in test_indices]'''

         #   print("Split data according to indices...")


