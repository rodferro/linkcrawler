from scraper import extract_links, list_links

from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = 'dev'

@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html')

@app.route('/extract')
def extract():
    return render_template('extract.html')

@app.route('/save', methods=["POST"])
def save():
    extract_links(request.form['url'])
    flash('Links saved')
    return redirect(url_for('extract'))

@app.route('/list')
def list():
    return render_template('list.html', links=list_links())

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
	return render_template('500.html'), 500

if __name__ == '__main__':
    app.run()