swapin:
	mv notes old_notes
	ln -s ~/Documents/zet notes

swapout:
	rm notes
	mv old_notes notes
