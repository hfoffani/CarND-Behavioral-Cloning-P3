import csv
import cv2
import numpy as np
np.random.seed(5)

from scipy.stats import norm
from collections import defaultdict

from keras.models import Sequential
from keras.layers import Flatten, Dense, Lambda
from keras.layers import Convolution2D, MaxPooling2D
from keras.layers import Dropout
from keras.layers import BatchNormalization
from keras.optimizers import Adam
from keras.callbacks import ModelCheckpoint


print('reading data...')


WIDTH=320
HEIGHT=160
CHANNELS=3

OFFSETCAMS=0.25
MAXTRANSLATE=50
MAXBRIGHT=.5
SIGMADELZEROS=0.25

LEARNINGRATE=0.0001
EPOCHS=7
VALIDATIONSPLIT=0.2
BATCH_SIZE=64



class Pipe:
    """ From https://github.com/JulienPalard/Pipe
    """

    def __init__(self, func=lambda x: x):
        self.func = func

    def __ror__(self, other):
        return self.func(other)

    def __call__(self, *args, **kwargs):
        return Pipe(lambda x: self.func(x, *args, **kwargs))


def readcsv(fname):
    train = []
    valid = []
    with open(fname) as csvfile:
        reader = csv.reader(csvfile)
        header = True
        for line in reader:
            if header:
                header = False
                continue
            steer = float(line[3])
            c_cam = 'data/' + line[0].strip()
            l_cam = 'data/' + line[1].strip()
            r_cam = 'data/' + line[2].strip()
            observ = c_cam, l_cam, r_cam, steer
            if np.random.uniform() < VALIDATIONSPLIT:
                valid.append( observ )
            else:
                train.append( observ )
    return train, valid


def img_from_filename(fname):
    img = cv2.imread(fname)
    assert img is not None
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


@Pipe
def read_images_and_steer(iterable, only_center_cam=False):
    for c_cam, l_cam, r_cam, steer in iterable:
        # center
        c_image = img_from_filename(c_cam)
        yield c_image, steer
        if only_center_cam:
            continue
        # left
        l_image = img_from_filename(l_cam)
        yield l_image, steer + OFFSETCAMS
        # right
        r_image = img_from_filename(r_cam)
        yield r_image, steer - OFFSETCAMS

@Pipe
def write_images(iterable, name, skip=False):
    d = defaultdict(int)
    for im, steer in iterable:
        if d[name] <= 50 and abs(steer) > .15 and np.random.uniform() < .005:
            d[name] += 1
            fname = 'models/images/%s-%04d.jpg' % (name, d[name])
            cv2.imwrite(fname, im)
        yield im, steer


@Pipe
def flip_images_horizontally(iterable, skip=False, replace=False):
    for im, steer in iterable:
        if not replace:
            yield im, steer
        if skip:
            continue
        image_flip = cv2.flip(im, 1)
        yield image_flip, -steer

@Pipe
def add_translated_images(iterable, trans_pixels, skip=False, replace=False):
    for im, steer in iterable:
        if not replace:
            yield im, steer
        if skip:
            continue
        trx = np.random.uniform(-trans_pixels, trans_pixels)
        M = np.float32([[1, 0, trx], [0, 1, 0]])
        image_trx = cv2.warpAffine(im, M, (WIDTH, HEIGHT))
        # Factor proposed by Vivek Yadav
        # https://carnd-forums.udacity.com/display/~vivek1108
        ANGLEPERPIXEL=0.004
        steer_trx = steer + trx * ANGLEPERPIXEL
        yield image_trx, steer_trx


@Pipe
def add_brightness_images(iterable, bright_percent, skip=False, replace=False):
    for im, steer in iterable:
        if not replace:
            yield im, steer
        if skip:
            continue
        image_br = cv2.cvtColor(im, cv2.COLOR_RGB2HSV)
        alter = np.random.uniform(1 - bright_percent, 1 + bright_percent)
        image_br[:, :, 2] = image_br[:, :, 2] * alter
        image_br[:, :, 2][image_br[:, :, 2] > 255] = 255
        image_br = cv2.cvtColor(image_br, cv2.COLOR_HSV2RGB)
        yield image_br, steer


@Pipe
def remove_with_normal(iterable, sigma, skip=False):
    mx = norm.pdf(0.0, 0.0, sigma)
    for im, steer in iterable:
        threshold = norm.pdf(steer, 0.0, sigma) / mx
        if not skip and np.random.uniform() < threshold:
            continue
        yield im, steer

def write_angles_to_file(iterable, fname):
    with open(fname, 'w') as angfile:
        angfile.write("steer\n")
        for i, (_, steer) in enumerate(iterable):
            angfile.write("%f\n" % steer)
        return i+1


#
# INPUT DATA PIPELINE
#
def pipeline(input_data):
    return input_data \
            | read_images_and_steer() \
            | add_translated_images(MAXTRANSLATE, replace=False) \
            | add_brightness_images(MAXBRIGHT, replace=False) \
            | flip_images_horizontally() \
            | remove_with_normal(SIGMADELZEROS)

train_data, valid_data = readcsv('data/driving_log.csv')

validationset = valid_data \
            | read_images_and_steer(only_center_cam=True)
X_val, y_val = tuple( np.array(x) for x in zip(*validationset) )

# for exact numbers but slower processing enable the commented line.
samples = 50000
# samples = write_angles_to_file(pipeline(train_data), 'angles.csv')
print("aprox. number of angles per epoch for training:", samples)
print('validatation set:', len(y_val))
print()
# exit()



def keras_generator(input_data, batch_size):
    slice_size = batch_size * 5
    X_batch = np.zeros((batch_size, HEIGHT, WIDTH, CHANNELS))
    y_batch = np.zeros(batch_size)
    i = 0
    while True:
        step = np.random.random_integers(0, len(input_data) / slice_size)
        offset = (step * slice_size) % (len(input_data) - slice_size)
        data_slice = input_data[offset: (offset + slice_size)]
        pipe = pipeline(data_slice)
        for image, steer in pipe:
            X_batch[i] = image
            y_batch[i] = steer
            i += 1
            if i >= batch_size:
                yield X_batch, y_batch
                i = 0



def resize4nvidia(img):
    import tensorflow as tf
    return tf.image.resize_images(img, [66, 200])


model = Sequential()

# model.add(Cropping2D . couldn't make it work
model.add(Lambda(lambda x: x[:, :, 60:-30, 0:], input_shape=(HEIGHT, WIDTH, CHANNELS)))
model.add(Lambda(resize4nvidia))
model.add(Lambda(lambda x: (x / 255.0) - 0.5))

model.add(Convolution2D(24, 5, 5, subsample=(2, 2),
        init="he_normal", activation="relu"))
model.add(Convolution2D(36, 5, 5, subsample=(2, 2),
        init="he_normal", activation="relu"))
model.add(Convolution2D(48, 5, 5, subsample=(2, 2),
        init="he_normal", activation="relu"))
model.add(Convolution2D(64, 3, 3,
        init="he_normal", activation="relu"))
model.add(Convolution2D(64, 3, 3,
        init="he_normal", activation="relu"))

model.add(Flatten())

model.add(Dense(100, activation="elu"))
model.add(Dropout(0.5))

model.add(Dense(50, activation="elu"))
model.add(Dropout(0.5))

model.add(Dense(20, activation="elu"))
model.add(Dropout(0.5))

model.add(Dense(1))

# model.summary()
# exit()

model.compile(loss='mse',
            optimizer=Adam(lr=LEARNINGRATE))
checkpoint_path="weights-{epoch:02d}.h5"
checkpoint = ModelCheckpoint(checkpoint_path,
            verbose=1, save_best_only=False, save_weights_only=True, mode='auto')


epoch_generator = keras_generator(train_data, BATCH_SIZE)

# samples_per_epoch should be divisible by batch size
s_p_e = ((samples // BATCH_SIZE) + 1) * BATCH_SIZE

model.fit_generator(
            epoch_generator,
            samples_per_epoch=s_p_e,
            # validation_split=VALIDATIONSPLIT,
            validation_data=(X_val, y_val),
            # shuffle=True,
            callbacks=[checkpoint],
            nb_epoch=EPOCHS)

model.save('model.h5')

exit()
