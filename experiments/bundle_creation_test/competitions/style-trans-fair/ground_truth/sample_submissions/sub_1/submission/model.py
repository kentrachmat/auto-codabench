'''
Sample predictive model.
You must supply at least 4 methods:
- fit: trains the model.
- predict: uses the model to perform predictions.
- save: saves the model.
- load: reloads the model.
'''
import pickle
import numpy as np   # We recommend to use numpy arrays
from os.path import isfile
from sklearn.base import BaseEstimator
from sklearn.svm import LinearSVC, SVC
from skimage.transform import resize
import tensorflow as tf
from tensorflow.keras import regularizers as reug
from sklearn.preprocessing import OneHotEncoder


class model (BaseEstimator):
    def __init__(self, number_of_classes, input_shape):
        '''
        This constructor is supposed to initialize data members.
        Use triple quotes for function documentation. 
        '''

        self.epochs = 3
        self.batch_size = 4
        self.initial_learning_rate = 0.001

        self.num_labels=number_of_classes
        self.is_trained=False
        self.enc = OneHotEncoder(handle_unknown='ignore')
        

        self.__model = tf.keras.Sequential()
        self.__model.add(tf.keras.layers.Conv2D(128, (3, 3), activation='relu', input_shape=input_shape))
        self.__model.add(tf.keras.layers.MaxPooling2D((2, 2)))
        # self.__model.add(tf.keras.layers.Conv2D(128, (3, 3), activation='relu'))
        # self.__model.add(tf.keras.layers.MaxPooling2D((2, 2)))
        self.__model.add(tf.keras.layers.Conv2D(64, (3, 3), activation='relu'))
        self.__model.add(tf.keras.layers.MaxPooling2D((2, 2)))
        # self.__model.add(tf.keras.layers.Conv2D(64, (3, 3), activation='relu'))
        # self.__model.add(tf.keras.layers.MaxPooling2D((2, 2)))
        self.__model.add(tf.keras.layers.Flatten())
        # self.__model.add(tf.keras.layers.Dense(
        #     512, 
        #     kernel_regularizer= reug.L1L2(l1=1e-5, l2=1e-4),
        #     activation='relu'))
        self.__model.add(tf.keras.layers.Dense(
            256, 
            kernel_regularizer= reug.L1L2(l1=1e-5, l2=1e-4),
            activation='relu'))
        self.__model.add(tf.keras.layers.Dense(number_of_classes, activation='softmax'))
        



    def fit(self, X, y):
        '''
        This function should train the model parameters.
        Here we do nothing in this example...
        Args:
            X: Training data matrix of dim num_train_samples * num_feat.
            y: Training label matrix of dim num_train_samples * num_labels.
        Both inputs are numpy arrays.
        For classification, labels could be either numbers 0, 1, ... c-1 for c classe
        or one-hot encoded vector of zeros, with a 1 at the kth position for class k.
        The AutoML format support on-hot encoding, which also works for multi-labels problems.
        Use data_converter.convert_to_num() to convert to the category number format.
        For regression, labels are continuous values.
        '''
        '''
                here the imput -X is an np.array() of shape [number of images, height, width]
                               -y is an np.array() of shape[number of images,]
                what we did is to resize all the images and flatten them from 3D to 1D and
                after this we transform the list of flatten image as array. And then we pass it into 
                the fit function.
                
         '''


        self.enc.fit(y.reshape(-1,1))
        y = self.enc.transform(y.reshape(-1,1)).toarray()


        total_steps = len(X) * self.epochs // self.batch_size
        lr_decayed_fn = tf.keras.optimizers.schedules.CosineDecay(
            self.initial_learning_rate, total_steps)
        optimizer = tf.keras.optimizers.legacy.Adam(learning_rate=lr_decayed_fn)
        self.__model.compile(optimizer=optimizer, loss='categorical_crossentropy', metrics=['accuracy'])

        # Run training on CPU
        with tf.device('/cpu:0'):
            self.__model.fit(X, y, epochs=self.epochs, batch_size=self.batch_size)
        

    def predict(self, X):
        '''
        This function should provide predictions of labels on (test) data.
        Here we just return zeros...
        Make sure that the predicted values are in the correct format for the scoring
        metric. For example, binary classification problems often expect predictions
        in the form of a discriminant value (if the area under the ROC curve it the metric)
        rather that predictions of the class labels themselves. For multi-class or multi-labels
        problems, class probabilities are often expected if the metric is cross-entropy.
        Scikit-learn also has a function predict-proba, we do not require it.
        The function predict eventually can return probabilities.
        '''
        
        '''
                here the imputs :  -X is an np.array() of shape [number of images, height, width]
                                 - y is an np.array() of shape[number of images,]
                what we did is to resize all the image and flatten them from 3D to 1D and
                after this we transform the list of flatten image as array. And then we pass it into 
                the predict function.
                
         '''
        

        # Run inference on CPU
        with tf.device('/cpu:0'):
            result = self.__model.predict(X)

        return np.argmax(result, axis=1)
            


    def save(self, path="./"):
        pickle.dump(self, open(path + '_model.pickle', "wb"))

    def load(self, path="./"):
        modelfile = path + '_model.pickle'
        if isfile(modelfile):
            with open(modelfile, 'rb') as f:
                self = pickle.load(f)
            print("Model reloaded from: " + modelfile)
        return self
