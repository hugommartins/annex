## Fiction
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND -#nonFiction
```
## Non-Fiction
### Philosophy

#### Stoicism
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND #stoicism 
```
#### Aesthetics
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND #aesthetics  
```
#### Ethics
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND #ethics  
```
#### Existentialism
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND #existentialism 
```

#### General 
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND #philosophy AND -#aesthetics AND -#stoicism AND -#ethics AND -#existentialism
```
### Nature
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND #nature
```
### Art
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND #art
```
### Lose threads
```dataview
TABLE 
ReleaseDate as Year,
Pros as Pros,
Cons as Cons,
Goodreads as Rating
From #book AND #maybe AND -#art AND -#philosophy AND -#language AND -#fiction AND -#nature
```

