
- [[Read books.base|Read books]]
- [[Read manga.base|Read manga]]
- [[Books data]]
- [[Reading list.base|Reading list]]
- [[Series.base|Series]]

### Currently reading

```dataviewjs
let groups = dv.pages('"books"')
    .filter(b => b.Status === "Reading" || b.Status === "reading");
    
let seriesGroups = groups.groupBy(b => b.Status);

seriesGroups.forEach(group => {
    let seriesName = group.key || "Unknown Series";

    let rows = group.rows.sort(b => b.Order, 'asc');

    let imageContainer = `<div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; justify-items: center;">`;

    rows.forEach(b => {
        if (b.cover) {
            const imgUrl = b.cover;
            const bookUrl = `obsidian://open?vault=content&file=${encodeURIComponent(b.file.path)}`;

            imageContainer += `
                <div>
                    <a href="${bookUrl}" style="text-decoration: none; display: block; text-align: center;">
                        <img src="${imgUrl}" style="height: auto; width: auto; border-radius: 6px; display: block; margin: 0 auto;"/>
                    </a>
                </div>
            `;
        } else {
            console.log(`No cover found for ${b.file.name}`);
        }
    });

    imageContainer += `</div>`;
    dv.paragraph(imageContainer);
});
```

### Next in line

```dataviewjs
let groups = dv.pages('"books"')
    .filter(b => b.Status === "Queued");
    
let seriesGroups = groups.groupBy(b => b.Status);

seriesGroups.forEach(group => {
    let seriesName = group.key || "Unknown Series";

    let rows = group.rows.sort(b => b.Order, 'asc');

    let imageContainer = `<div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; justify-items: center;">`;

    rows.forEach(b => {
        if (b.cover) {
            const imgUrl = b.cover;
            const bookUrl = `obsidian://open?vault=bookshelf&file=${encodeURIComponent(b.file.path)}`;

            imageContainer += `
                <div>
                    <a href="${bookUrl}" style="text-decoration: none; display: block; text-align: center;">
                        <img src="${imgUrl}" style="height: auto; width: auto; border-radius: 6px; display: block; margin: 0 auto;"/>
                    </a>
                </div>
            `;
        } else {
            console.log(`No cover found for ${b.file.name}`);
        }
    });
    imageContainer += `</div>`;
    dv.paragraph(imageContainer);
});
```

### Books to organize 

![[To be organized.base]]

```dataview
List
From #discworld 
Where Status = "Unread"
Sort Order Asc
```
