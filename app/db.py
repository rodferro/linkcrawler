import dataset

db = dataset.connect('sqlite:///data.db')
link = db['link']